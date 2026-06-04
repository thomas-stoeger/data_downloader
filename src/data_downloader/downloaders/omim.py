"""
OMIM downloader.

OMIM (Online Mendelian Inheritance in Man) gates its rich data files
(`genemap2.txt`, `morbidmap.txt`) behind a personal, registration-issued
download URL whose terms forbid redistribution, so those are out of scope for
this tool. The `mim2gene.txt` cross-reference file, however, is served openly:

    https://omim.org/static/omim/data/mim2gene.txt

It maps each MIM number to its entry type and to Entrez Gene, HGNC symbol, and
Ensembl gene identifiers. This downloader fetches that single file.

Versioning: the file carries a `# Generated: YYYY-MM-DD` header line; the
version string is that date, read from the first few kilobytes of the file (a
ranged GET) without downloading the whole file.

The file is plain text, so it is verified by the size check only (no archive
read-through).

Config keys:
    none (the URL is fixed).
"""
import re
import ssl
import urllib.request
from pathlib import Path

import certifi
from tqdm import tqdm

from .base import BaseDownloader, _with_retries

_URL = "https://omim.org/static/omim/data/mim2gene.txt"
_FILENAME = "mim2gene.txt"
_CHUNK = 1024 * 1024  # 1 MB
_HEADER_CAP = 4096
_SSL = ssl.create_default_context(cafile=certifi.where())
_HEADERS = {"User-Agent": "data_downloader (+https://omim.org)"}
_GENERATED_RE = re.compile(r"#\s*Generated:\s*(\d{4}-\d{2}-\d{2})")


def _request(url: str, extra: dict | None = None) -> urllib.request.Request:
    headers = dict(_HEADERS)
    if extra:
        headers.update(extra)
    return urllib.request.Request(url, headers=headers)


def _latest_version() -> str:
    """Return the `# Generated:` date from the file header, reading only the
    first few kilobytes (a ranged GET)."""
    req = _request(_URL, {"Range": f"bytes=0-{_HEADER_CAP - 1}"})
    with urllib.request.urlopen(req, context=_SSL) as r:
        head = r.read(_HEADER_CAP).decode("utf-8", errors="replace")
    m = _GENERATED_RE.search(head)
    if not m:
        raise RuntimeError(f"No '# Generated:' header found in {_URL}")
    return m.group(1)


def _download_file(url: str, local_path: Path) -> Path:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    bytes_written = 0
    with urllib.request.urlopen(_request(url), context=_SSL) as response:
        expected_size = int(response.headers.get("Content-Length", 0)) or None
        with (
            open(local_path, "wb") as f,
            tqdm(total=expected_size, unit="B", unit_scale=True, desc=local_path.name) as bar,
        ):
            while chunk := response.read(_CHUNK):
                f.write(chunk)
                bytes_written += len(chunk)
                bar.update(len(chunk))
    if expected_size is not None and bytes_written != expected_size:
        local_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"{local_path.name}: size mismatch, expected {expected_size} bytes, "
            f"got {bytes_written}"
        )
    return local_path


class OmimDownloader(BaseDownloader):

    def latest_version(self, config: dict) -> str:
        return _latest_version()

    def fetch(self, config: dict, dest: Path) -> list[Path]:
        version = _latest_version()
        print(f"omim {version}: downloading {_FILENAME}")
        path = _with_retries(lambda: _download_file(_URL, dest / _FILENAME))
        return [path]
