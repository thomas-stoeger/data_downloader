"""
NSF Awards downloader.

The NSF Award Search "Download Awards" feature is now a single-page app backed
by a public JSON endpoint that lists the per-year bulk award archives, each with
a freshly minted pre-signed S3 download URL:

    https://www.research.gov/awardapi-service/v2/s3/list-files

The response carries one entry per file: a `fileName` (e.g. `2025.zip`,
`Historical.zip`, plus a small `timestamp.txt` build marker), its `size`, its
`lastModified`, and a ready-to-use `downloadUrl` on the
`dis-prod-awardsearch` S3 bucket. No API key is required; the pre-signed URLs
are generated per request, so this downloader always fetches a fresh listing and
uses the URLs immediately.

Each per-year archive holds that fiscal year's awards (one record per award:
title, abstract, amounts, dates, PI, institution). NSF switched the in-archive
record format from XML to JSON in January 2025; the archives are still `.zip`.

Versioning: the version string is the date NSF last rebuilt the export, taken
from the `lastModified` of the `timestamp.txt` entry in the listing (formatted
`YYYY-MM-DD`), without downloading any data file. NSF rebuilds daily, so like
the ExPORTER downloader this can report a new version each day.

Each file is verified against the `size` from the listing plus the zip
read-through (`verify_file`).

Config keys:
    files (optional) list of file names to download. Defaults to every `.zip`
          archive in the listing plus `timestamp.txt`. Restrict with e.g.
          ["2024.zip", "2025.zip", "2026.zip", "Historical.zip"].
"""
import json
import ssl
import urllib.request
from pathlib import Path

import certifi
from tqdm import tqdm

from .base import BaseDownloader, _with_retries

_LIST_URL = "https://www.research.gov/awardapi-service/v2/s3/list-files"
_CHUNK = 1024 * 1024  # 1 MB
_SSL = ssl.create_default_context(cafile=certifi.where())
_HEADERS = {"User-Agent": "data_downloader (+https://www.nsf.gov)", "Accept": "application/json"}


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers=_HEADERS)


def _list_files() -> list[dict]:
    """Return the list of file entries from the NSF Award Search list-files
    endpoint (each has fileName, size, lastModified, downloadUrl)."""
    with urllib.request.urlopen(_request(_LIST_URL), context=_SSL) as r:
        data = json.loads(r.read())
    files = data.get("files")
    if not files:
        raise RuntimeError(f"No files listed at {_LIST_URL}")
    return files


def _version_from(files: list[dict]) -> str:
    """Return the export build date (YYYY-MM-DD) from the timestamp.txt entry's
    lastModified, falling back to the max lastModified across files."""
    stamps = [f["lastModified"] for f in files if f["fileName"] == "timestamp.txt"]
    if not stamps:
        stamps = [f["lastModified"] for f in files if f.get("lastModified")]
    if not stamps:
        raise RuntimeError("No lastModified timestamps in the NSF file listing")
    return max(stamps)[:10]


def _download_file(url: str, local_path: Path, expected_size: int | None) -> Path:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    bytes_written = 0
    with urllib.request.urlopen(_request(url), context=_SSL) as response:
        size = expected_size or int(response.headers.get("Content-Length", 0)) or None
        with (
            open(local_path, "wb") as f,
            tqdm(total=size, unit="B", unit_scale=True, desc=local_path.name) as bar,
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


class NsfAwardsDownloader(BaseDownloader):

    def latest_version(self, config: dict) -> str:
        return _version_from(_list_files())

    def fetch(self, config: dict, dest: Path) -> list[Path]:
        files = _list_files()
        version = _version_from(files)

        wanted = config.get("files")
        if wanted:
            selected = [f for f in files if f["fileName"] in wanted]
            missing = [w for w in wanted if w not in {f["fileName"] for f in files}]
            if missing:
                raise RuntimeError(
                    f"nsf_awards: file(s) {missing} not in the listing. "
                    f"Available: {sorted(f['fileName'] for f in files)}"
                )
        else:
            selected = [
                f for f in files
                if f["fileName"].endswith(".zip") or f["fileName"] == "timestamp.txt"
            ]

        print(f"nsf_awards {version}: downloading {len(selected)} file(s)")
        written: list[Path] = []
        for f in selected:
            name = f["fileName"]
            url = f["downloadUrl"]
            size = f.get("size")
            written.append(
                _with_retries(lambda u=url, n=name, s=size: _download_file(u, dest / n, s))
            )
        return written
