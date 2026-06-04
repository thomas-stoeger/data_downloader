"""
Reactome downloader.

Reactome publishes its release files at a stable "current" path:

    https://reactome.org/download/current/

This downloader fetches a configured set of those files. The default set maps
genes/proteins to Reactome pathways (across all hierarchy levels) and provides
the pathway names and the parent/child relations needed to walk the hierarchy:

  - NCBI2Reactome_All_Levels.txt    Entrez Gene id -> pathway (all levels).
  - UniProt2Reactome_All_Levels.txt UniProt accession -> pathway (all levels).
  - Ensembl2Reactome_All_Levels.txt Ensembl id -> pathway (all levels).
  - ReactomePathways.txt            pathway id -> name -> species.
  - ReactomePathwaysRelation.txt    parent -> child pathway relations.

Versioning: the version string is the Reactome release number (e.g. `96`), read
from the ContentService database-version endpoint
(`https://reactome.org/ContentService/data/database/version`) without
downloading any data file.

Reactome publishes no per-file checksum sidecars, and these are plain-text
files, so each is verified by the size check only (no archive read-through).

Config keys:
    files (optional) list of file names in download/current/ to download.
          Defaults to the set described above.
"""
import ssl
import urllib.request
from pathlib import Path

import certifi
from tqdm import tqdm

from .base import BaseDownloader, _with_retries

_BASE = "https://reactome.org/download/current"
_VERSION_URL = "https://reactome.org/ContentService/data/database/version"
_CHUNK = 1024 * 1024  # 1 MB
_SSL = ssl.create_default_context(cafile=certifi.where())
_HEADERS = {"User-Agent": "data_downloader (+https://reactome.org)"}

_DEFAULT_FILES = [
    "NCBI2Reactome_All_Levels.txt",
    "UniProt2Reactome_All_Levels.txt",
    "Ensembl2Reactome_All_Levels.txt",
    "ReactomePathways.txt",
    "ReactomePathwaysRelation.txt",
]


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers=_HEADERS)


def _latest_version() -> str:
    """Return the Reactome release number (e.g. "96") from the ContentService
    database-version endpoint, without downloading any data file."""
    with urllib.request.urlopen(_request(_VERSION_URL), context=_SSL) as r:
        version = r.read().decode("utf-8", errors="replace").strip()
    if not version:
        raise RuntimeError(f"Empty response from {_VERSION_URL}; cannot derive version")
    return version


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


class ReactomeDownloader(BaseDownloader):

    def latest_version(self, config: dict) -> str:
        return _latest_version()

    def fetch(self, config: dict, dest: Path) -> list[Path]:
        version = _latest_version()
        files = config.get("files") or _DEFAULT_FILES
        print(
            f"reactome {version}: downloading {len(files)} file(s): "
            f"{', '.join(files)}"
        )
        written: list[Path] = []
        for name in files:
            url = f"{_BASE}/{name}"
            written.append(
                _with_retries(lambda u=url, n=name: _download_file(u, dest / n))
            )
        return written
