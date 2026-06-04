"""
GWAS Catalog downloader.

The NHGRI-EBI GWAS Catalog publishes each release on the EBI FTP server (served
over HTTPS):

    https://ftp.ebi.ac.uk/pub/databases/gwas/releases/latest/

`latest/` is a symlink to the newest dated release under `releases/YYYY/MM/DD/`.
This downloader fetches a configured set of files from `latest/`. The default
set links published variant-trait associations (EFO-annotated) to their studies,
ancestry, and trait-ontology mappings:

  - gwas-catalog-associations_ontology-annotated-full.zip  variant-trait
                             associations with mapped genes, risk alleles,
                             p-values, and EFO trait annotations (the full,
                             un-split form).
  - gwas-catalog-studies.tsv  one row per study (accession, PubMed id, trait,
                             sample sizes).
  - gwas-catalog-ancestry.tsv  per-study ancestry / sample-composition detail.
  - gwas-efo-trait-mappings.tsv  reported trait -> EFO term/URI mappings.

Versioning: the GWAS Catalog has no checksum or release-tag sidecar in the
listing, so the version is the `Last-Modified` date of a stable file in
`latest/` (formatted `YYYY-MM-DD`), read with a HEAD request without
downloading any data. This mirrors the `ftp` downloader's modification-time
versioning and tracks whatever release `latest/` currently points to.

No MD5 sidecars are published, so each file is verified by the size check plus
the archive read-through (`verify_file` reads the `.zip` files through).

Config keys:
    files (optional) list of file names in the latest release to download.
          Defaults to the set described above. Names not present cause a
          failure that prints what is available.
"""
import re
import ssl
import urllib.request
from email.utils import parsedate_to_datetime
from pathlib import Path

import certifi
from tqdm import tqdm

from .base import BaseDownloader, _with_retries

_BASE = "https://ftp.ebi.ac.uk/pub/databases/gwas/releases/latest"
_VERSION_FILE = "gwas-catalog-studies.tsv"
_CHUNK = 1024 * 1024  # 1 MB
_SSL = ssl.create_default_context(cafile=certifi.where())
# Identify the tool; the default Python-urllib user agent is best avoided.
_HEADERS = {"User-Agent": "data_downloader (+https://www.ebi.ac.uk/gwas)"}

_DEFAULT_FILES = [
    "gwas-catalog-associations_ontology-annotated-full.zip",
    "gwas-catalog-studies.tsv",
    "gwas-catalog-ancestry.tsv",
    "gwas-efo-trait-mappings.tsv",
]

_HREF_RE = re.compile(r'href="([^"]+)"')


def _request(url: str, method: str = "GET") -> urllib.request.Request:
    return urllib.request.Request(url, headers=_HEADERS, method=method)


def _list_dir(url: str) -> list[str]:
    """Return the entry names in an EBI FTP-over-HTTPS autoindex directory."""
    with urllib.request.urlopen(_request(url), context=_SSL) as r:
        text = r.read().decode("utf-8", errors="replace")
    names: list[str] = []
    for href in _HREF_RE.findall(text):
        if href.startswith("?") or href.startswith("/") or href == "../":
            continue
        names.append(href)
    return names


def _latest_version() -> str:
    """Return the release date (e.g. "2026-06-01") as the Last-Modified date of
    a stable file in latest/, read with a HEAD request (no data download)."""
    with urllib.request.urlopen(
        _request(f"{_BASE}/{_VERSION_FILE}", method="HEAD"), context=_SSL
    ) as r:
        last_modified = r.headers.get("Last-Modified")
    if not last_modified:
        raise RuntimeError(
            f"No Last-Modified header for {_BASE}/{_VERSION_FILE}; cannot derive version"
        )
    return parsedate_to_datetime(last_modified).strftime("%Y-%m-%d")


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


class GwasCatalogDownloader(BaseDownloader):

    def latest_version(self, config: dict) -> str:
        return _latest_version()

    def fetch(self, config: dict, dest: Path) -> list[Path]:
        version = dest.name
        files = config.get("files") or _DEFAULT_FILES

        available = set(_list_dir(f"{_BASE}/"))
        missing = [f for f in files if f not in available]
        if missing:
            raise RuntimeError(
                f"gwas_catalog: file(s) {missing} not found in release {version}. "
                f"Available: {', '.join(sorted(available))}"
            )

        print(
            f"gwas_catalog {version}: downloading {len(files)} file(s): "
            f"{', '.join(files)}"
        )
        written: list[Path] = []
        for name in files:
            url = f"{_BASE}/{name}"
            written.append(
                _with_retries(lambda u=url, n=name: _download_file(u, dest / n))
            )
        return written
