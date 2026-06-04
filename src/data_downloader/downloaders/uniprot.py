"""
UniProt downloader.

UniProt publishes its knowledgebase on the EBI-style FTP server (served over
HTTPS) under a `current_release` pointer:

    https://ftp.uniprot.org/pub/databases/uniprot/current_release/knowledgebase/

This downloader fetches a configured set of files from one subdirectory of the
knowledgebase. Two datasets use it:

  - the protein sequences under `complete/` (e.g. `uniprot_sprot.fasta.gz`,
    the reviewed Swiss-Prot sequences; `uniprot_trembl.fasta.gz`, the much
    larger unreviewed set), and
  - the identifier cross-references under `idmapping/` (e.g.
    `idmapping.dat.gz`, the full UniProtKB-AC / database / external-ID table).

Versioning: every part of a UniProt release shares one release tag, e.g.
`2026_01`, read from `complete/reldate.txt` ("UniProt Knowledgebase Release
2026_01 ..."), without downloading any data file. The same tag versions both
the sequence and idmapping datasets, since UniProt rebuilds the whole release
together.

Each subdirectory ships a `RELEASE.metalink` listing every file's size and MD5;
downloaded files are verified against that MD5 where present, in addition to the
size check and the gzip archive read-through (`verify_file`).

Config keys:
    path  (required) the knowledgebase subdirectory to download from, e.g.
          "complete" or "idmapping".
    files (required) list of file names in that subdirectory to download, e.g.
          ["uniprot_sprot.fasta.gz"] or ["idmapping.dat.gz"]. Names not present
          cause a failure that prints what is available.
"""
import hashlib
import re
import ssl
import urllib.request
from pathlib import Path

import certifi
from tqdm import tqdm

from .base import BaseDownloader, _with_retries

_BASE = "https://ftp.uniprot.org/pub/databases/uniprot/current_release/knowledgebase"
_RELDATE = f"{_BASE}/complete/reldate.txt"
_CHUNK = 1024 * 1024  # 1 MB
_SSL = ssl.create_default_context(cafile=certifi.where())
# Identify the tool; the default Python-urllib user agent is best avoided.
_HEADERS = {"User-Agent": "data_downloader (+https://www.uniprot.org)"}

_HREF_RE = re.compile(r'href="([^"]+)"')
_RELEASE_RE = re.compile(r"Release\s+(\d{4}_\d{2})")
_FILE_BLOCK_RE = re.compile(r'<file\s+name="([^"]+)">(.*?)</file>', re.DOTALL)
_MD5_RE = re.compile(r'<hash\s+type="md5">\s*([0-9a-fA-F]{32})\s*</hash>')


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers=_HEADERS)


def _read_text(url: str) -> str:
    with urllib.request.urlopen(_request(url), context=_SSL) as r:
        return r.read().decode("utf-8", errors="replace")


def _list_dir(url: str) -> list[str]:
    """Return the entry names in an EBI FTP-over-HTTPS autoindex directory.

    Directory names keep their trailing slash; the column-sort links and the
    parent/absolute links the autoindex emits are dropped.
    """
    names: list[str] = []
    for href in _HREF_RE.findall(_read_text(url)):
        if href.startswith("?") or href.startswith("/") or href == "../":
            continue
        names.append(href)
    return names


def _latest_version() -> str:
    """Return the UniProt release tag (e.g. "2026_01") from reldate.txt."""
    m = _RELEASE_RE.search(_read_text(_RELDATE))
    if not m:
        raise RuntimeError(f"Could not parse a UniProt release tag from {_RELDATE}")
    return m.group(1)


def _parse_metalink(text: str) -> dict[str, str]:
    """Parse a RELEASE.metalink into {basename: md5}.

    Keys are basenames so a checksum matches the file regardless of the path the
    metalink records.
    """
    out: dict[str, str] = {}
    for block in _FILE_BLOCK_RE.finditer(text):
        name, body = block.group(1), block.group(2)
        h = _MD5_RE.search(body)
        if h:
            out[Path(name).name] = h.group(1).lower()
    return out


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


class UniProtDownloader(BaseDownloader):

    def latest_version(self, config: dict) -> str:
        return _latest_version()

    def fetch(self, config: dict, dest: Path) -> list[Path]:
        version = dest.name
        path = config.get("path")
        files = config.get("files")
        if not path or not files:
            raise RuntimeError(
                "uniprot: set both 'path' (e.g. \"complete\") and 'files' in the registry"
            )

        dir_url = f"{_BASE}/{path}"
        available = [n for n in _list_dir(f"{dir_url}/") if not n.endswith("/")]
        missing = [f for f in files if f not in available]
        if missing:
            raise RuntimeError(
                f"uniprot: file(s) {missing} not found in {path}/. "
                f"Available: {', '.join(available)}"
            )

        # Per-file MD5s from the directory's metalink. Best effort: a missing or
        # unparseable metalink leaves files verified by size and the gzip check.
        checksums: dict[str, str] = {}
        try:
            checksums = _parse_metalink(_read_text(f"{dir_url}/RELEASE.metalink"))
        except Exception as exc:
            print(f"  uniprot: no metalink checksums ({exc}); size-checking only.")

        print(
            f"uniprot {version}: downloading {len(files)} file(s) from {path}/: "
            f"{', '.join(files)}"
        )
        written: list[Path] = []
        for name in files:
            url = f"{dir_url}/{name}"
            expected = checksums.get(name)
            written.append(
                _with_retries(
                    lambda u=url, n=name, e=expected: _download_file(u, dest / n, e)
                )
            )
        return written
