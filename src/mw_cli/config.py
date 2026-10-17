import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from .errors import WikiError, require


def config_path():
    return Path(os.environ.get("MW_CLI_CONFIG", Path.home() / ".config/mw-cli/config.json"))


def state_dir():
    return Path(os.environ.get("MW_CLI_STATE", Path.home() / ".local/state/mw-cli"))


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (ValueError, OSError) as exc:
        raise WikiError("invalid-state", f"Cannot read JSON file: {path}") from exc


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".mw-cli-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@dataclass
class Config:
    api: str
    username: str | None = None
    password: str | None = field(default=None, repr=False)
    guidelines: str | None = None
    timeout: float = 30
    state: Path = field(default_factory=state_dir)

    def receipt_path(self):
        identity = json.dumps([self.api, self.username, self.guidelines]).encode()
        return self.state / ("guidelines-" + hashlib.sha256(identity).hexdigest() + ".json")


def resolve(options):
    data = read_json(options.get("config") or config_path(), {"profiles": {}})
    profile = options.get("profile") or os.environ.get("MW_PROFILE", "default")
    require(
        isinstance(data, dict) and isinstance(data.get("profiles", {}), dict),
        "Invalid profiles configuration.",
    )
    saved = data.get("profiles", {}).get(profile, {})
    require(isinstance(saved, dict), "Invalid profile configuration.")
    api = options.get("api") or os.environ.get("MW_API_URL") or saved.get("api")
    require(api, "Set --api https://wiki.example/w/api.php, MW_API_URL, or config set.")
    url = urlsplit(api)
    require(
        url.scheme in ("https", "http")
        and url.hostname
        and not url.username
        and not url.password
        and not url.query
        and not url.fragment,
        "API URL must be HTTP(S), without credentials, query or fragment.",
    )
    username = options.get("username") or os.environ.get("MW_USERNAME") or saved.get("username")
    password_env = saved.get("password_env", "MW_BOT_PASSWORD")
    password = os.environ.get(password_env)
    require(not username or password, f"Set {password_env} to the Bot Password (not as a CLI argument).")
    if username and url.scheme != "https" and url.hostname not in ("localhost", "127.0.0.1", "::1"):
        require(
            options.get("allow_http"),
            "Bot Passwords require HTTPS; --allow-http permits an HTTP intranet endpoint.",
        )
    return Config(
        api=api,
        username=username,
        password=password,
        guidelines=options.get("guidelines") or os.environ.get("MW_GUIDELINES") or saved.get("guidelines"),
        timeout=options.get("timeout", 30),
    )
