"""
HGNC downloader.

The HUGO Gene Nomenclature Committee (HGNC) publishes its complete dataset in a
public Google Cloud Storage bucket (`public-download-files`). The "current"
files are overwritten in place twice a week, but the bucket also keeps an
immutable, month-stamped archive of the complete set:

    hgnc/archive/archive/monthly/tsv/hgnc_complete_set_<YYYY-MM-DD>.txt

This downloader fetches the newest monthly archive snapshot of the complete set
(TSV): one row per approved human gene with its HGNC id, approved symbol and
name, locus type/group, chromosomal location, the symbol/name assignment dates,
and cross-references (Entrez, Ensembl, RefSeq, UniProt, etc.). Taking the
immutable monthly snapshot rather than the live `current/` file gives a stable
versioned copy.

Versioning: the version string is the date embedded in the newest monthly file
name (e.g. `2026-06-02`), found by listing the archive prefix via the GCS JSON
API without downloading any data file.

Each file is verified against the bucket's published MD5 (the `md5Hash` field in
the listing, base64-encoded), plus the size check. The complete set is plain
text, so there is no archive read-through.

Config keys:
    none (the bucket and archive prefix are fixed).
"""
import base64
import hashlib
import json
import re
import ssl
import urllib.parse
import urllib.request
from pathlib import Path

import certifi
from tqdm import tqdm

from .base import BaseDownloader, _with_retries

_BUCKET = "public-download-files"
_PREFIX = "hgnc/archive/archive/monthly/tsv/"
_NAME_RE = re.compile(r"hgnc_complete_set_(\d{4}-\d{2}-\d{2})\.txt$")
_CHUNK = 1024 * 1024  # 1 MB
_SSL = ssl.create_default_context(cafile=certifi.where())
_HEADERS = {"User-Agent": "data_downloader (+https://www.genenames.org)"}


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers=_HEADERS)


def _list_objects(prefix: str) -> list[dict]:
    """Return the GCS objects under `prefix` via the storage JSON API."""
    url = (
        f"https://storage.googleapis.com/storage/v1/b/{_BUCKET}/o"
        f"?prefix={urllib.parse.quote(prefix)}&maxResults=1000"
    )
    with urllib.request.urlopen(_request(url), context=_SSL) as r:
        return json.loads(r.read()).get("items", [])


def _latest_object() -> tuple[str, str, str | None]:
    """Return (version_date, object_name, md5_hex) for the newest monthly
    complete-set snapshot."""
    best: tuple[str, dict] | None = None
    for obj in _list_objects(f"{_PREFIX}hgnc_complete_set_"):
        m = _NAME_RE.search(obj["name"])
        if m and (best is None or m.group(1) > best[0]):
            best = (m.group(1), obj)
    if best is None:
        raise RuntimeError(
            f"No hgnc_complete_set_<date>.txt found under gs://{_BUCKET}/{_PREFIX}"
        )
    date, obj = best
    md5_hex = None
    if obj.get("md5Hash"):
        md5_hex = base64.b64decode(obj["md5Hash"]).hex()
    return date, obj["name"], md5_hex


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


class HgncDownloader(BaseDownloader):

    def latest_version(self, config: dict) -> str:
        return _latest_object()[0]

    def fetch(self, config: dict, dest: Path) -> list[Path]:
        version, name, md5_hex = _latest_object()
        url = f"https://storage.googleapis.com/{_BUCKET}/{name}"
        local_name = name.rsplit("/", 1)[-1]
        print(f"hgnc {version}: downloading {local_name}")
        path = _with_retries(
            lambda: _download_file(url, dest / local_name, md5_hex)
        )
        return [path]
