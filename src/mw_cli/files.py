import hashlib
import os
import tempfile
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests

from .errors import WikiError, require
from .guidelines import guard


def file_title(name):
    return name if name.lower().startswith("file:") else "File:" + name


def info(client, title):
    result = client.request(
        {
            "action": "query",
            "prop": "imageinfo",
            "titles": file_title(title),
            "iiprop": "url|size|sha1|mime|timestamp|user|comment",
        }
    )
    pages = result.get("query", {}).get("pages", [])
    if not pages or not pages[0].get("imageinfo"):
        raise WikiError("missing-file", f"File not found: {title}", exit_code=3)
    return {"title": pages[0]["title"], **pages[0]["imageinfo"][0]}


def download(client, title, output, *, overwrite=False, max_bytes=1024**3):
    metadata = info(client, title)
    require((metadata.get("size") or 0) <= max_bytes, "File exceeds --max-bytes.")
    path = Path(output)
    require(overwrite or not path.exists(), "Output exists; use --overwrite to replace it.")
    url = metadata["url"]
    origin = urlsplit(client.config.api)
    response = None
    public = requests.Session()
    public.trust_env = False
    public.verify = client.session.verify
    public.headers["User-Agent"] = client.session.headers["User-Agent"]
    try:
        for _ in range(6):
            target = urlsplit(url)
            require(
                target.scheme in ("http", "https")
                and target.hostname
                and not target.username
                and not target.password,
                "Invalid file download URL.",
            )
            require(
                not (origin.scheme == "https" and target.scheme == "http"),
                "Refusing HTTPS-to-HTTP download downgrade.",
            )
            same_origin = (target.scheme, target.hostname, target.port) == (
                origin.scheme,
                origin.hostname,
                origin.port,
            )
            session = client.session if same_origin else public
            response = session.get(
                url,
                stream=True,
                timeout=client.config.timeout,
                allow_redirects=False,
                proxies=requests.utils.get_environ_proxies(url),
            )
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("Location")
                response.close()
                require(location, "Download redirect is missing Location.")
                url = urljoin(url, location)
                continue
            break
        else:
            raise WikiError("redirect-loop", "Too many file download redirects.")
        if response.status_code != 200:
            raise WikiError("download-failed", f"Download returned HTTP {response.status_code}.")
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".mw-download-")
        try:
            size, digest = 0, hashlib.sha1()
            with os.fdopen(fd, "wb") as stream:
                for chunk in response.iter_content(1024 * 1024):
                    size += len(chunk)
                    require(size <= max_bytes, "Download exceeds --max-bytes.")
                    digest.update(chunk)
                    stream.write(chunk)
            if metadata.get("size") is not None and metadata["size"] != size:
                raise WikiError(
                    "integrity-error", "Downloaded size differs from imageinfo; the file may have changed."
                )
            expected = metadata.get("sha1")
            if expected:
                # MediaWiki can serialize SHA-1 as padded base36 or hexadecimal.
                numeric = int(expected, 16 if len(expected) == 40 else 36)
                if numeric != int(digest.hexdigest(), 16):
                    raise WikiError("integrity-error", "Downloaded SHA-1 does not match imageinfo.")
            if overwrite:
                os.replace(temporary, path)
            else:
                # Atomic no-clobber publication, even if another process created the destination.
                os.link(temporary, path)
            return {
                "title": metadata["title"],
                "path": str(path.resolve()),
                "bytes": size,
                "sha1": digest.hexdigest(),
            }
        finally:
            Path(temporary).unlink(missing_ok=True)
    except requests.RequestException as exc:
        raise WikiError("download-failed", "Download interrupted; no partial output was published.") from exc
    finally:
        if response is not None:
            response.close()
        public.close()


def upload(
    client,
    source,
    *,
    filename=None,
    comment="",
    description=None,
    overwrite=False,
    ignore_warnings=False,
    chunk_size=5 * 1024**2,
    dry_run=False,
):
    path = Path(source)
    require(path.is_file(), "Upload source must be a regular local file.")
    name = filename or path.name
    require(
        name and "/" not in name and "\\" not in name and not name.lower().startswith("file:"),
        "--filename must be a filename without a path or File: prefix.",
    )
    require(chunk_size > 0, "Chunk size must be positive.")
    require(client.logged_in, "Upload requires Bot Password authentication.")
    guard(client)
    try:
        existing = info(client, name)
    except WikiError as exc:
        if exc.code != "missing-file":
            raise
        existing = None
    require(not existing or overwrite, "Wiki file exists; use --overwrite to upload a new version.")
    size = path.stat().st_size
    require(size > 0, "Cannot upload an empty file.")
    if dry_run:
        return {
            "dry_run": True,
            "filename": name,
            "bytes": size,
            "overwrite": overwrite,
            "chunk_size": chunk_size,
        }
    filekey, offset = None, 0
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            result = client.mutate(
                {
                    "action": "upload",
                    "filename": name,
                    "stash": True,
                    "filesize": size,
                    "offset": offset,
                    "filekey": filekey,
                    "ignorewarnings": ignore_warnings,
                },
                files={"chunk": ("chunk.bin", chunk, "application/octet-stream")},
            )
            uploaded = result.get("upload", {})
            if uploaded.get("result") not in ("Continue", "Success"):
                raise WikiError(
                    "upload-warning",
                    "Upload not committed. Inspect warnings; use --ignore-warnings only deliberately.",
                    details={"warnings": uploaded.get("warnings", {})},
                )
            filekey = uploaded.get("filekey")
            offset += len(chunk)
            if not filekey or uploaded.get("offset", offset) != offset:
                raise WikiError("upload-offset", "Unexpected upload offset; do not blindly retry.")
    require(
        offset == size and path.stat().st_size == size,
        "Local file changed during upload; nothing was committed.",
    )
    guard(client)
    result = client.mutate(
        {
            "action": "upload",
            "filename": name,
            "filekey": filekey,
            "comment": comment,
            "text": description,
            "ignorewarnings": ignore_warnings,
        }
    )
    if result.get("upload", {}).get("result") != "Success":
        raise WikiError(
            "upload-warning",
            "Upload not confirmed. No warnings were silently bypassed.",
            details={"warnings": result.get("upload", {}).get("warnings", {})},
        )
    return result
