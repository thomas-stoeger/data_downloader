"""
OBO ontology downloader.

Fetches a single OBO-format ontology file from a stable URL. The OBO Foundry
serves each ontology behind a PURL (e.g.
https://purl.obolibrary.org/obo/go/go-basic.obo) that redirects to the current
release; urllib follows the redirect automatically.

The version string is read from the ontology's own header. Every OBO file
begins with a small set of header lines, one of which is
`data-version: releases/YYYY-MM-DD` (the Gene Ontology dates each release this
way). The downloader reads only the first few kilobytes to parse that line, so
"is this current?" is answered without downloading the whole ontology. The
`releases/` path prefix is stripped, leaving a filesystem-safe version such as
`2026-05-19`.

Required config keys:
    url       - the OBO file URL

Optional config keys (defaults shown):
    filename  - name to save the file under  (basename of the URL)
"""
import ssl
import urllib.parse
import urllib.request
from pathlib import Path

import certifi
from tqdm import tqdm

from .base import BaseDownloader, _with_retries

_CHUNK = 1024 * 1024  # 1 MB
_HEADER_CAP = 64 * 1024  # read at most this many bytes to find data-version
_SSL = ssl.create_default_context(cafile=certifi.where())
# The OBO Foundry / Gene Ontology hosts reject the default Python-urllib
# user agent with 403, so identify the tool explicitly.
_HEADERS = {"User-Agent": "data_downloader (+https://geneontology.org)"}


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers=_HEADERS)


def _data_version(url: str) -> str:
    """Read the ontology header and return its `data-version` as a
    filesystem-safe string. Only the first chunk of the file is read."""
    read = 0
    buf = b""
    with urllib.request.urlopen(_request(url), context=_SSL) as response:
        while read < _HEADER_CAP:
            chunk = response.read(_CHUNK)
            if not chunk:
                break
            buf += chunk
            read += len(chunk)
            # The header sits at the very top; stop as soon as we have the line.
            if b"\ndata-version:" in b"\n" + buf:
                break
    for line in buf.decode("utf-8", "replace").splitlines():
        if line.startswith("data-version:"):
            value = line.split(":", 1)[1].strip()
            if not value:
                break
            # e.g. "releases/2026-05-19" -> "2026-05-19"
            return value.rsplit("/", 1)[-1]
    raise RuntimeError(f"No 'data-version' header found in OBO file at {url}")


def _download(url: str, name: str, dest: Path) -> Path:
    local_path = dest / name
    try:
        with urllib.request.urlopen(_request(url), context=_SSL) as response:
            expected = int(response.headers.get("Content-Length", 0)) or None
            bytes_written = 0
            with (
                open(local_path, "wb") as f,
                tqdm(total=expected, unit="B", unit_scale=True, desc=name) as bar,
            ):
                while chunk := response.read(_CHUNK):
                    f.write(chunk)
                    bytes_written += len(chunk)
                    bar.update(len(chunk))
        if expected is not None and bytes_written != expected:
            raise RuntimeError(
                f"{name}: size mismatch — expected {expected} bytes, got {bytes_written}"
            )
    except Exception:
        local_path.unlink(missing_ok=True)
        raise
    return local_path


class OboDownloader(BaseDownloader):

    def latest_version(self, config: dict) -> str:
        return _data_version(config["url"])

    def fetch(self, config: dict, dest: Path) -> list[Path]:
        url = config["url"]
        name = config.get("filename") or Path(urllib.parse.urlparse(url).path).name
        path = _with_retries(lambda: _download(url, name, dest))
        return [path]
