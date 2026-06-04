"""
Unknome downloader.

The Unknome database (MRC Laboratory of Molecular Biology) ranks proteins by how
little is known about them. It publishes dated releases on its download page:

    https://unknome.mrc-lmb.cam.ac.uk/download/

Each release is identified by its date in `DD_Mon_YYYY` form (e.g.
`18_Mar_2026`) and is served through per-summary endpoints that stream a
gzipped TSV directly (the filename comes back in the `Content-Disposition`
header):

    /download/prot_tsv_gz/<release>/   -> unknome_protein_table_<release>.tsv.gz
    /download/clust_tsv_gz/<release>/  -> unknome_cluster_table_<release>.tsv.gz
    /download/db_sql/<release>/        -> the full SQLite database (~6 GB)

This downloader fetches the compressed protein summary and compressed cluster
summary by default; the full SQLite database is not downloaded unless requested.

Versioning: the version string is the release date identifier (e.g.
`18_Mar_2026`), the newest one parsed from the download page, read without
downloading any data.

No checksum sidecars are published, so each file is verified by the size check
plus the gzip archive read-through (`verify_file`).

Config keys:
    summaries (optional) list of summaries to download, from "protein",
              "cluster", "database". Defaults to ["protein", "cluster"].
"""
import re
import ssl
import urllib.request
from datetime import datetime
from pathlib import Path

import certifi
from tqdm import tqdm

from .base import BaseDownloader, _with_retries

_BASE = "https://unknome.mrc-lmb.cam.ac.uk/download"
_CHUNK = 1024 * 1024  # 1 MB
_SSL = ssl.create_default_context(cafile=certifi.where())
# Identify the tool; the default Python-urllib user agent is best avoided.
_HEADERS = {"User-Agent": "data_downloader (+https://unknome.mrc-lmb.cam.ac.uk)"}

# Summary name -> the download path segment that serves it.
_SUMMARIES = {
    "protein": "prot_tsv_gz",
    "cluster": "clust_tsv_gz",
    "database": "db_sql",
}
_DEFAULT_SUMMARIES = ["protein", "cluster"]

# Release identifiers look like 18_Mar_2026 (day_Mon_year).
_RELEASE_RE = re.compile(r"/download/prot_tsv_gz/(\d{2}_[A-Za-z]{3}_\d{4})/")
_RELEASE_FMT = "%d_%b_%Y"
_CD_FILENAME_RE = re.compile(r'filename\*?=(?:"([^"]+)"|([^;]+))', re.IGNORECASE)


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers=_HEADERS)


def _releases() -> list[str]:
    """Return the release identifiers advertised on the download page."""
    with urllib.request.urlopen(_request(f"{_BASE}/"), context=_SSL) as r:
        html = r.read().decode("utf-8", errors="replace")
    return sorted(set(_RELEASE_RE.findall(html)))


def _latest_version() -> str:
    """Return the newest release identifier (e.g. "18_Mar_2026")."""
    releases = _releases()
    if not releases:
        raise RuntimeError(f"No Unknome releases found on {_BASE}/")
    return max(releases, key=lambda r: datetime.strptime(r, _RELEASE_FMT))


def _filename_from_disposition(value: str) -> str | None:
    """Extract a safe basename from a Content-Disposition header.

    The header is untrusted remote data, so the value is reduced to its
    basename and any traversal or empty result is rejected.
    """
    m = _CD_FILENAME_RE.search(value or "")
    if not m:
        return None
    raw = (m.group(1) or m.group(2) or "").strip()
    name = Path(raw).name
    if not name or name in (".", ".."):
        return None
    return name


def _download(url: str, dest: Path, fallback_name: str) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(_request(url), context=_SSL) as response:
        name = (
            _filename_from_disposition(response.headers.get("Content-Disposition", ""))
            or fallback_name
        )
        local_path = dest / name
        expected_size = int(response.headers.get("Content-Length", 0)) or None
        bytes_written = 0
        with (
            open(local_path, "wb") as f,
            tqdm(total=expected_size, unit="B", unit_scale=True, desc=name) as bar,
        ):
            while chunk := response.read(_CHUNK):
                f.write(chunk)
                bytes_written += len(chunk)
                bar.update(len(chunk))
    if expected_size is not None and bytes_written != expected_size:
        local_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"{name}: size mismatch, expected {expected_size} bytes, got {bytes_written}"
        )
    return local_path


class UnknomeDownloader(BaseDownloader):

    def latest_version(self, config: dict) -> str:
        return _latest_version()

    def fetch(self, config: dict, dest: Path) -> list[Path]:
        # The store names the version directory after latest_version(), so the
        # release identifier is the directory name; reuse it.
        version = dest.name
        requested = config.get("summaries") or _DEFAULT_SUMMARIES

        unknown = [s for s in requested if s not in _SUMMARIES]
        if unknown:
            raise RuntimeError(
                f"unknome: unknown summary {unknown}. "
                f"Available: {', '.join(_SUMMARIES)}"
            )

        print(
            f"unknome {version}: downloading {len(requested)} file(s): "
            f"{', '.join(requested)}"
        )
        written: list[Path] = []
        for summary in requested:
            segment = _SUMMARIES[summary]
            url = f"{_BASE}/{segment}/{version}/"
            fallback = f"unknome_{summary}_{version}.gz"
            written.append(
                _with_retries(lambda u=url, fb=fallback: _download(u, dest, fb))
            )
        return written
