"""Provision an isolated SQLite MediaWiki and execute live tests. Requires PHP CLI.

Used by CI only; never reads an existing LocalSettings.php or touches a user's wiki.
All wiki state lives in a freshly created temporary directory.
"""

import os
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen


def main():
    version = sys.argv[1] if len(sys.argv) > 1 else "1.43.1"
    minor = ".".join(version.split(".")[:2])
    with tempfile.TemporaryDirectory(prefix="mw-cli-live-") as directory:
        root = Path(directory)
        wiki = root / ("mediawiki-" + version)
        # Wikimedia release downloads can reject CI cloud IPs. Official GitHub mirrors
        # provide both core and the prebuilt dependency tree without that endpoint.
        subprocess.run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--branch",
                version,
                "https://github.com/wikimedia/mediawiki.git",
                str(wiki),
            ],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--branch",
                "REL" + minor.replace(".", "_"),
                "https://github.com/wikimedia/mediawiki-vendor.git",
                str(wiki / "vendor"),
            ],
            check=True,
        )
        database = root / "db"
        database.mkdir()
        password = secrets.token_hex(20)
        bot_password = secrets.token_hex(16)
        subprocess.run(
            [
                "php",
                "maintenance/install.php",
                "--dbtype",
                "sqlite",
                "--dbpath",
                str(database),
                "--dbname",
                "mwcli",
                "--server",
                "http://127.0.0.1:8765",
                "--scriptpath",
                "",
                "--pass",
                password,
                "CLI Test Wiki",
                "Admin",
            ],
            cwd=wiki,
            check=True,
        )
        with (wiki / "LocalSettings.php").open("a") as settings:
            settings.write(
                "\n$wgEnableUploads = true;\n$wgEnableBotPasswords = true;\n"
                "$wgRateLimits = [];\n$wgPasswordAttemptThrottle = [];\n"
            )
        # PHP is the server's native setup API; CLI and test orchestration stay Python.
        setup = r"""
        $services = \MediaWiki\MediaWikiServices::getInstance();
        $class = class_exists('MediaWiki\\User\\BotPassword') ? 'MediaWiki\\User\\BotPassword' : 'BotPassword';
        $bot = $class::newUnsaved(['username' => 'Admin', 'appId' => 'mw-cli',
          'grants' => ['basic', 'editpage', 'createeditmovepage', 'uploadfile', 'uploadeditmovefile', 'delete', 'patrol']]);
        $password = $services->getPasswordFactory()->newFromPlaintext(getenv('MW_LIVE_PASSWORD'));
        $status = $bot->save('insert', $password);
        if (!$status->isOK()) { throw new RuntimeException('Bot Password setup failed'); }
        $editor = $services->getUserFactory()->newFromName('PatrolEditor');
        $editor->addToDatabase();
        $bot = $class::newUnsaved(['username' => 'PatrolEditor', 'appId' => 'mw-cli',
          'grants' => ['basic', 'editpage', 'createeditmovepage']]);
        $status = $bot->save('insert', $services->getPasswordFactory()->newFromPlaintext(getenv('MW_LIVE_EDITOR_PASSWORD')));
        if (!$status->isOK()) { throw new RuntimeException('Editor Bot Password setup failed'); }
        """
        env = {
            **os.environ,
            "MW_LIVE_DISPOSABLE": "1",
            "MW_LIVE_API": "http://127.0.0.1:8765/api.php",
            "MW_LIVE_USERNAME": "Admin@mw-cli",
            "MW_LIVE_PASSWORD": bot_password,
            "MW_LIVE_PATROL": "1",
            "MW_LIVE_AGENT": "1",
            "MW_LIVE_EDITOR_USERNAME": "PatrolEditor@mw-cli",
            "MW_LIVE_EDITOR_PASSWORD": secrets.token_hex(20),
            "NO_PROXY": "127.0.0.1,localhost",
        }
        # eval.php reads bounded input lines (1024 bytes in supported releases).
        # Load a file instead of putting the growing setup program on stdin.
        setup_path = root / "setup-bots.php"
        setup_path.write_text("<?php\n" + setup, encoding="utf-8")
        env["MW_CLI_SETUP_FILE"] = str(setup_path)
        subprocess.run(
            ["php", "maintenance/eval.php"],
            input="require getenv('MW_CLI_SETUP_FILE');\n",
            text=True,
            env=env,
            cwd=wiki,
            check=True,
        )
        with (root / "server.log").open("w") as log:
            server = subprocess.Popen(
                ["php", "-S", "127.0.0.1:8765", "-t", str(wiki)], stdout=log, stderr=log
            )
            try:
                for _ in range(60):
                    try:
                        with urlopen("http://127.0.0.1:8765/api.php?action=query&format=json", timeout=2):
                            break
                    except OSError:
                        if server.poll() is not None:
                            raise RuntimeError("Disposable wiki server exited")
                        time.sleep(1)
                else:
                    raise RuntimeError("Disposable wiki did not become ready")
                subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "pytest",
                        "tests/test_live.py",
                        "tests/test_patrol_live.py",
                        "tests/test_agent_live.py",
                        "-v",
                    ],
                    env=env,
                    check=True,
                )
            finally:
                server.terminate()
                server.wait(timeout=10)


if __name__ == "__main__":
    main()
