"""
InterPro downloader.

InterPro (EMBL-EBI) publishes each release on the EBI FTP server (served over
HTTPS):

    https://ftp.ebi.ac.uk/pub/databases/interpro/current_release/

This downloader fetches a configured set of files from that directory. The
default set supports mapping proteins to InterPro entries and expanding those
entries to their ancestors, plus the entry metadata and schema:

  - protein2ipr.dat.gz       per-protein matches to integrated InterPro (IPR)
                             entries: UniProtKB accession, IPR accession, IPR
                             name, matching member-database signature, and
                             start/end coordinates. (Large, ~17 GB compressed.
                             This is the integrated-entry file; the much larger
                             signature-level `match_complete.xml.gz`, which also
                             includes signatures not yet integrated into an IPR
                             entry, is not downloaded by default.)
  - ParentChildTreeFile.txt  the InterPro entry hierarchy as an indented tree,
                             needed to expand a matched (leaf) IPR entry to its
                             parent/ancestor entries.
  - entry.list               flat IPR accession -> name -> type lookup.
  - interpro.xml.gz          full entry metadata (type, abstracts, GO mappings,
                             member signatures), with `interpro.dtd` its schema.
  - names.dat / short_names.dat  IPR accession -> (short) name lookups.
  - interpro2go              IPR entry -> GO term mappings.
  - release_notes.txt        release provenance.

Versioning: the version string is the InterPro release number (e.g. `108.0`),
read from `release_notes.txt` ("Release 108.0, ...") without downloading any
data file.

Files that ship a `<name>.md5` sidecar (the large `.gz`/`.tar.gz` files) are
verified against that MD5, in addition to the size check and the gzip archive
read-through (`verify_file`).

Config keys:
    files (optional) list of file names in the release directory to download.
          Defaults to the set described above. Names not present cause a
          failure that prints what is available.
"""
import hashlib
import re
import ssl
import urllib.request
from pathlib import Path

import certifi
from tqdm import tqdm

from .base import BaseDownloader, _with_retries

_BASE = "https://ftp.ebi.ac.uk/pub/databases/interpro/current_release"
_CHUNK = 1024 * 1024  # 1 MB
_SSL = ssl.create_default_context(cafile=certifi.where())
# Identify the tool; the default Python-urllib user agent is best avoided.
_HEADERS = {"User-Agent": "data_downloader (+https://www.ebi.ac.uk/interpro)"}

_DEFAULT_FILES = [
    "protein2ipr.dat.gz",
    "ParentChildTreeFile.txt",
    "entry.list",
    "interpro.xml.gz",
    "interpro.dtd",
    "names.dat",
    "short_names.dat",
    "interpro2go",
    "release_notes.txt",
]

_HREF_RE = re.compile(r'href="([^"]+)"')
_RELEASE_RE = re.compile(r"Release\s+([\d.]+)")


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers=_HEADERS)


def _read_text(url: str) -> str:
    with urllib.request.urlopen(_request(url), context=_SSL) as r:
        return r.read().decode("utf-8", errors="replace")


def _list_dir(url: str) -> list[str]:
    """Return the entry names in an EBI FTP-over-HTTPS autoindex directory."""
    names: list[str] = []
    for href in _HREF_RE.findall(_read_text(url)):
        if href.startswith("?") or href.startswith("/") or href == "../":
            continue
        names.append(href)
    return names


def _latest_version() -> str:
    """Return the InterPro release number (e.g. "108.0") from release_notes.txt."""
    m = _RELEASE_RE.search(_read_text(f"{_BASE}/release_notes.txt"))
    if not m:
        raise RuntimeError(f"Could not parse a release number from {_BASE}/release_notes.txt")
    return m.group(1)


def _download_file(url: str, local_path: Path, expected_md5: str | None) -> Path:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    md5 = hashlib.md5()
    bytes_written = 0
    with urllib.request.urlopen(_request(url), context=_SSL) as response:
        expected_size = int(response.headers.get("Content-Length", 0)) or None
        with (
            open(local_path, "wb") as f,
            tqdm(total=expected_size, unit="B", unit_scale=True, desc=local_path.name) as bar,
        ):
            while chunk := response.read(_CHUNK):
                f.write(chunk)
                md5.update(chunk)
                bytes_written += len(chunk)
                bar.update(len(chunk))
    if expected_size is not None and bytes_written != expected_size:
        local_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"{local_path.name}: size mismatch, expected {expected_size} bytes, "
            f"got {bytes_written}"
        )
    if expected_md5 and md5.hexdigest() != expected_md5:
        local_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"{local_path.name}: MD5 mismatch, expected {expected_md5}, "
            f"got {md5.hexdigest()}"
        )
    return local_path


class InterProDownloader(BaseDownloader):

    def latest_version(self, config: dict) -> str:
        return _latest_version()

    def fetch(self, config: dict, dest: Path) -> list[Path]:
        version = dest.name
        files = config.get("files") or _DEFAULT_FILES

        available = set(_list_dir(f"{_BASE}/"))
        missing = [f for f in files if f not in available]
        if missing:
            raise RuntimeError(
                f"interpro: file(s) {missing} not found in release {version}. "
                f"Available: {', '.join(sorted(available))}"
            )

        print(
            f"interpro {version}: downloading {len(files)} file(s): "
            f"{', '.join(files)}"
        )
        written: list[Path] = []
        for name in files:
            # Verify against the upstream MD5 sidecar when the release ships one
            # for this file (the large .gz/.tar.gz files have a <name>.md5).
            expected_md5 = None
            if f"{name}.md5" in available:
                first = _read_text(f"{_BASE}/{name}.md5").split()
                if first and re.fullmatch(r"[0-9a-fA-F]{32}", first[0]):
                    expected_md5 = first[0].lower()
            url = f"{_BASE}/{name}"
            written.append(
                _with_retries(
                    lambda u=url, n=name, e=expected_md5: _download_file(u, dest / n, e)
                )
            )
        return written
