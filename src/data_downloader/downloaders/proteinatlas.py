"""
Human Protein Atlas (HPA) downloader.

The Human Protein Atlas publishes its bulk data on the download page:

    https://www.proteinatlas.org/about/download

The page states the current release (e.g. "Human Protein Atlas version 25.1")
and links to a small set of bulk files served from stable, unversioned URLs
under https://www.proteinatlas.org/download/ :

    proteinatlas.tsv.zip   the per-gene summary table (the searchable subset)
    proteinatlas.json.gz   the same summary data as JSON
    proteinatlas.xml.gz    the comprehensive record (full protein expression,
                           RNA-seq, antibody, and image metadata; large)

Because the URLs carry no version, the version string is read from the release
number stated on the download page; archived releases live on `vNN.proteinatlas.org`
subdomains but are not used here.

Versioning: the version string is the HPA release number (e.g. `25.1`), parsed
from the download page without downloading any data file.

No checksum sidecars are published, so each file is verified by the size check
plus the archive read-through (`verify_file`: zip for `.zip`, gzip for `.gz`).

Config keys:
    files (optional) list of file names to download. Defaults to
          ["proteinatlas.tsv.zip"]. Names that are not offered on the download
          page cause a failure that prints what is available.
"""
import re
import ssl
import urllib.request
from pathlib import Path

import certifi
from tqdm import tqdm

from .base import BaseDownloader, _with_retries

_BASE = "https://www.proteinatlas.org"
_PAGE = f"{_BASE}/about/download"
_CHUNK = 1024 * 1024  # 1 MB
_SSL = ssl.create_default_context(cafile=certifi.where())
# Identify the tool; the default Python-urllib user agent is best avoided.
_HEADERS = {"User-Agent": "data_downloader (+https://www.proteinatlas.org)"}

_DEFAULT_FILES = ["proteinatlas.tsv.zip"]
# "...Human Protein Atlas version 25.1." (Ensembl is written "v109", so the
# leading word "version" keeps the Ensembl build from matching.)
_VERSION_RE = re.compile(r"[Vv]ersion\s*(\d+\.\d+)")
_DOWNLOAD_LINK_RE = re.compile(r"/download/([^\"'\s>]+\.(?:zip|gz))")


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers=_HEADERS)


def _page_html() -> str:
    with urllib.request.urlopen(_request(_PAGE), context=_SSL) as r:
        return r.read().decode("utf-8", errors="replace")


def _parse_version(html: str) -> str:
    m = _VERSION_RE.search(html)
    if not m:
        raise RuntimeError(f"Could not find an HPA version on {_PAGE}")
    return m.group(1)


def _parse_available(html: str) -> list[str]:
    return sorted(set(_DOWNLOAD_LINK_RE.findall(html)))


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


class ProteinAtlasDownloader(BaseDownloader):

    def latest_version(self, config: dict) -> str:
        return _parse_version(_page_html())

    def fetch(self, config: dict, dest: Path) -> list[Path]:
        files = config.get("files") or _DEFAULT_FILES

        available = _parse_available(_page_html())
        missing = [f for f in files if f not in available]
        if missing:
            raise RuntimeError(
                f"proteinatlas: file(s) {missing} not offered on the download "
                f"page. Available: {', '.join(available)}"
            )

        version = dest.name
        print(
            f"proteinatlas {version}: downloading {len(files)} file(s): "
            f"{', '.join(files)}"
        )
        written: list[Path] = []
        for name in files:
            url = f"{_BASE}/download/{name}"
            written.append(
                _with_retries(lambda u=url, n=name: _download_file(u, dest / n))
            )
        return written
