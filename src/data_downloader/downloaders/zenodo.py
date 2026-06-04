"""
Zenodo record downloader.

Fetches the files of the latest version of a Zenodo record, identified by its
concept (all-versions) record id. Zenodo assigns every record a stable concept
id that always resolves to the newest version: requesting

    https://zenodo.org/api/records/<concept_record_id>

redirects to the latest version's record, whose JSON lists that version's
files (each with a download URL and an MD5 checksum) and a `metadata.version`
tag. urllib follows the redirect automatically.

This is used for the Research Organization Registry (ROR), whose data dump is
published on Zenodo under concept record 6347574 as a single
`<version>-<date>-ror-data.zip` (JSON + CSV, schema v1 and v2). The downloader
is generic and works for any Zenodo record.

Versioning: the version string is the record's `metadata.version` (e.g.
`v2.8`), read from the record JSON without downloading any file. Each Zenodo
version has its own tag, so this changes only on a new release.

Files are verified against the Zenodo-published MD5, plus the size check and the
archive read-through (`verify_file` reads `.zip` files through).

Required config keys:
    concept_record_id  the Zenodo concept (all-versions) record id.

Optional config keys:
    files              list of file names to download (downloads all if
                       omitted).
"""
import hashlib
import json
import ssl
import urllib.request
from pathlib import Path

import certifi
from tqdm import tqdm

from .base import BaseDownloader, _with_retries

_API = "https://zenodo.org/api/records"
_CHUNK = 1024 * 1024  # 1 MB
_SSL = ssl.create_default_context(cafile=certifi.where())
# Identify the tool; the default Python-urllib user agent is best avoided.
_HEADERS = {"User-Agent": "data_downloader (+https://zenodo.org)"}


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers=_HEADERS)


def _latest_record(concept_record_id: str) -> dict:
    """Return the JSON of the latest version of a concept record (the request
    redirects from the concept id to the newest version, followed by urllib)."""
    with urllib.request.urlopen(
        _request(f"{_API}/{concept_record_id}"), context=_SSL
    ) as r:
        return json.loads(r.read())


def _md5_from_checksum(checksum: str | None) -> str | None:
    """Zenodo reports a file checksum as 'md5:<hex>'. Return the hex digest, or
    None for any non-md5 algorithm."""
    if checksum and checksum.startswith("md5:"):
        return checksum.split(":", 1)[1].lower()
    return None


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


class ZenodoDownloader(BaseDownloader):

    def latest_version(self, config: dict) -> str:
        record = _latest_record(config["concept_record_id"])
        version = record.get("metadata", {}).get("version")
        if not version:
            raise RuntimeError(
                f"Zenodo record {record.get('id')} has no metadata.version to use as a version string"
            )
        return version

    def fetch(self, config: dict, dest: Path) -> list[Path]:
        concept_record_id = config["concept_record_id"]
        wanted: list[str] | None = config.get("files")

        record = _latest_record(concept_record_id)
        files = record.get("files", [])
        if not files:
            raise RuntimeError(f"Zenodo record {record.get('id')} lists no files")

        if wanted:
            selected = [f for f in files if f["key"] in wanted]
            missing = [w for w in wanted if w not in {f["key"] for f in files}]
            if missing:
                raise RuntimeError(
                    f"zenodo: file(s) {missing} not found in record {record.get('id')}. "
                    f"Available: {[f['key'] for f in files]}"
                )
            files = selected

        written: list[Path] = []
        for file_info in files:
            name = file_info["key"]
            url = file_info["links"]["self"]
            expected_md5 = _md5_from_checksum(file_info.get("checksum"))
            written.append(
                _with_retries(
                    lambda u=url, n=name, m=expected_md5: _download_file(u, dest / n, m)
                )
            )
        return written
