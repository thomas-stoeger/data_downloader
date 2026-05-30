"""
Google Sheets downloader for sheets that publish no version marker.

Some public Google Sheets (e.g. community-maintained lists of predatory
publishers and predatory journals) carry no "last updated" cell and no upstream
release date, so the plain `google_sheet` downloader, which reads a date from a
designated cell, has nothing to read. This downloader instead identifies a
snapshot by the content of the exported CSV. The version string is:

    YYYY-MM-DD (hash)

where `hash` is the first 12 hex characters of the SHA-256 of the exported CSV
bytes and the date is the day the snapshot was first captured.

This is a deliberate, sanctioned exception to the project rule that a version
comes cheaply from the remote (see docs/DESIGN.md, Versioning): the source
offers no version marker, so `latest_version` must download the CSV to hash it,
and the date is local rather than remote.

To keep "is this current?" meaningful despite the date component, the hash is
the real identity, not the date. When freshly hashed content matches a snapshot
already recorded in the dataset's manifest, `latest_version` returns that
existing version string unchanged (preserving its original capture date), so an
unchanged sheet reports up-to-date instead of looking new every day. Only when
the hash changes does a new `YYYY-MM-DD (hash)` version, dated today, appear.

Required config keys:
    sheet_id  - the spreadsheet ID (the /d/<id>/ path segment)
    gid       - the numeric tab id (the gid= query parameter)

Optional config keys (defaults shown):
    filename  - name to save the CSV under  ("sheet.csv")

The store injects `_dataset_name` (see versioning/store.py) so that
`latest_version` can read the dataset's manifest. If that key is absent the
downloader falls back to dating the snapshot today, which is always safe.
"""
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from ..config import get_data_root
from ..versioning import manifest as manifest_mod
from .base import BaseDownloader, _with_retries
from .google_sheet import _fetch_csv_bytes

_HASH_LEN = 12
# Matches the "(hash)" suffix of a version string produced by this downloader.
_VERSION_RE = re.compile(r"\(([0-9a-f]{%d})\)$" % _HASH_LEN)


def _content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:_HASH_LEN]


def _existing_version_for_hash(dataset_name: str, content_hash: str) -> str | None:
    """Return the manifest version string whose embedded hash matches
    `content_hash`, or None if this content has not been recorded yet."""
    dataset_root = get_data_root() / dataset_name
    for entry in manifest_mod.all_local_versions(dataset_root):
        match = _VERSION_RE.search(entry.get("version", ""))
        if match and match.group(1) == content_hash:
            return entry["version"]
    return None


class GoogleSheetHashedDownloader(BaseDownloader):

    def latest_version(self, config: dict) -> str:
        name = config.get("filename", "sheet.csv")
        data = _fetch_csv_bytes(config["sheet_id"], config["gid"], name)
        content_hash = _content_hash(data)

        dataset_name = config.get("_dataset_name")
        if dataset_name:
            existing = _existing_version_for_hash(dataset_name, content_hash)
            if existing:
                return existing

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return f"{today} ({content_hash})"

    def fetch(self, config: dict, dest: Path) -> list[Path]:
        sheet_id = config["sheet_id"]
        gid = config["gid"]
        name = config.get("filename", "sheet.csv")

        def _write() -> Path:
            data = _fetch_csv_bytes(sheet_id, gid, name)
            path = dest / name
            path.write_bytes(data)
            return path

        return [_with_retries(_write)]
