"""
Ontology Lookup Service (OLS) downloader.

EMBL-EBI's Ontology Lookup Service publishes dated snapshots of its loaded
ontologies on the EBI FTP server (served over HTTPS):

    https://ftp.ebi.ac.uk/pub/databases/spot/ols/<timestamp>/

Each snapshot directory is named with a timestamp (e.g.
`2026_05_22__06_33_19`) and holds several large archives. This downloader
fetches `ontology_jsons_linked.tgz` -- the OLS internal JSON representation of
every loaded ontology after the OLS linker has added cross-references between
ontologies and to external databases (~150 GB uncompressed). That linked JSON
is the form that supports resolving a term's ancestors across ontologies.

Versioning: the version string is the snapshot timestamp directory name (e.g.
`2026_05_22__06_33_19`), the newest such directory in the listing, read without
downloading anything. The OLS README notes the representation is internal to OLS
and may change between snapshots.

No checksum sidecars are published, so the downloaded `.tgz` is verified by the
size check plus the gzip archive read-through (`verify_file`).

Config keys:
    files (optional) list of archive file names to download from the latest
          snapshot. Defaults to ["ontology_jsons_linked.tgz"]. Other archives
          in the snapshot include ontology_jsons.tgz, neo4j.tgz, solr.tgz, and
          sssom.tgz.
"""
import re
import ssl
import urllib.request
from pathlib import Path

import certifi
from tqdm import tqdm

from .base import BaseDownloader, _with_retries

_BASE = "https://ftp.ebi.ac.uk/pub/databases/spot/ols"
_CHUNK = 1024 * 1024  # 1 MB
_SSL = ssl.create_default_context(cafile=certifi.where())
# Identify the tool; the default Python-urllib user agent is best avoided.
_HEADERS = {"User-Agent": "data_downloader (+https://www.ebi.ac.uk/ols)"}

_DEFAULT_FILES = ["ontology_jsons_linked.tgz"]
_HREF_RE = re.compile(r'href="([^"]+)"')
# Snapshot directory names look like 2026_05_22__06_33_19 (zero-padded, so they
# sort chronologically as plain strings).
_TIMESTAMP_RE = re.compile(r"^\d{4}_\d{2}_\d{2}__\d{2}_\d{2}_\d{2}$")


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers=_HEADERS)


def _list_dir(url: str) -> list[str]:
    """Return the entry names in an EBI FTP-over-HTTPS autoindex directory.

    Directory names keep their trailing slash; the column-sort links and the
    parent/absolute links the autoindex emits are dropped.
    """
    with urllib.request.urlopen(_request(url), context=_SSL) as r:
        html = r.read().decode("utf-8", errors="replace")
    names: list[str] = []
    for href in _HREF_RE.findall(html):
        if href.startswith("?") or href.startswith("/") or href == "../":
            continue
        names.append(href)
    return names


def _latest_version() -> str:
    """Return the newest OLS snapshot timestamp (e.g. "2026_05_22__06_33_19")."""
    stamps = [
        n.rstrip("/") for n in _list_dir(f"{_BASE}/")
        if _TIMESTAMP_RE.match(n.rstrip("/"))
    ]
    if not stamps:
        raise RuntimeError(f"No timestamped OLS snapshot directories under {_BASE}")
    return max(stamps)


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


class OlsDownloader(BaseDownloader):

    def latest_version(self, config: dict) -> str:
        return _latest_version()

    def fetch(self, config: dict, dest: Path) -> list[Path]:
        # The store names the version directory after latest_version(), so the
        # snapshot timestamp is the directory name; reuse it.
        version = dest.name
        files = config.get("files") or _DEFAULT_FILES
        snapshot_url = f"{_BASE}/{version}"

        available = [n for n in _list_dir(f"{snapshot_url}/") if not n.endswith("/")]
        missing = [f for f in files if f not in available]
        if missing:
            raise RuntimeError(
                f"ols: file(s) {missing} not found in snapshot {version}. "
                f"Available: {', '.join(available)}"
            )

        print(f"ols {version}: downloading {len(files)} file(s): {', '.join(files)}")
        written: list[Path] = []
        for name in files:
            url = f"{snapshot_url}/{name}"
            written.append(
                _with_retries(lambda u=url, n=name: _download_file(u, dest / n))
            )
        return written
