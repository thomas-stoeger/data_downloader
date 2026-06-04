"""
BioGRID downloader.

BioGRID publishes its interaction data on a downloads host that serves stable
"Latest-Release" file names (the directory autoindex is not available; it
redirects to an error page):

    https://downloads.thebiogrid.org/Download/BioGRID/Latest-Release/BIOGRID-ALL-LATEST.tab3.zip

This downloader fetches a configured set of those stable LATEST files (default:
the comprehensive all-interactions table in TAB3 format) and saves each with the
release number substituted into the name, so the local copy is self-describing.

Versioning: the version string is the BioGRID release number (e.g. `5.0.257`),
parsed from the BioGRID home page (`https://thebiogrid.org/`, which states
"Version X.Y.Z") without downloading any data file.

The LATEST files are served chunked without a Content-Length, so the size check
is skipped; each `.zip` is verified by the gzip/zip read-through (`verify_file`).
BioGRID publishes no per-file checksum sidecars.

Config keys:
    files (optional) list of stable LATEST file names to download. Defaults to
          BIOGRID-ALL-LATEST.tab3.zip. Each "LATEST" is replaced with the
          release number in the saved file name.
"""
import re
import ssl
import urllib.request
from pathlib import Path

import certifi
from tqdm import tqdm

from .base import BaseDownloader, _with_retries

_HOME = "https://thebiogrid.org/"
_BASE = "https://downloads.thebiogrid.org/Download/BioGRID/Latest-Release"
_CHUNK = 1024 * 1024  # 1 MB
_SSL = ssl.create_default_context(cafile=certifi.where())
_HEADERS = {"User-Agent": "data_downloader (+https://thebiogrid.org)"}

_DEFAULT_FILES = ["BIOGRID-ALL-LATEST.tab3.zip"]
_VERSION_RE = re.compile(r"Version\s+(\d+\.\d+\.\d+)")


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers=_HEADERS)


def _latest_version() -> str:
    """Return the BioGRID release number (e.g. "5.0.257") parsed from the home
    page, without downloading any data file."""
    with urllib.request.urlopen(_request(_HOME), context=_SSL) as r:
        text = r.read().decode("utf-8", errors="replace")
    m = _VERSION_RE.search(text)
    if not m:
        raise RuntimeError(f"Could not parse a BioGRID version from {_HOME}")
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


class BioGridDownloader(BaseDownloader):

    def latest_version(self, config: dict) -> str:
        return _latest_version()

    def fetch(self, config: dict, dest: Path) -> list[Path]:
        version = _latest_version()
        files = config.get("files") or _DEFAULT_FILES
        print(
            f"biogrid {version}: downloading {len(files)} file(s): {', '.join(files)}"
        )
        written: list[Path] = []
        for name in files:
            url = f"{_BASE}/{name}"
            # Save with the release number in place of "LATEST" so the local
            # file is self-describing.
            local_name = name.replace("LATEST", version)
            written.append(
                _with_retries(lambda u=url, n=local_name: _download_file(u, dest / n))
            )
        return written
