"""
Google Sheets downloader.

Exports a single tab of a public Google Sheet as CSV. The version string is
read cheaply from a designated cell (default A1) that the sheet maintainer
keeps updated with a human-readable "last updated" date, e.g.
"First created: May 30, 2022; last updated May 17, 2026". The last such date
in that cell becomes the version (YYYY-MM-DD), so "is this current?" can be
answered without downloading the sheet.

The CSV export endpoint on docs.google.com issues a 307 redirect to a
googleusercontent.com host that serves the bytes (with Content-Length and a
text/csv content type); urllib follows the redirect automatically.

Required config keys:
    sheet_id  - the spreadsheet ID (the /d/<id>/ path segment)
    gid       - the numeric tab id (the gid= query parameter)

Optional config keys (defaults shown):
    filename      - name to save the CSV under   ("sheet.csv")
    version_cell  - cell holding the update date  ("A1")
"""
import csv
import io
import re
import ssl
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

import certifi
from tqdm import tqdm

from .base import BaseDownloader, _with_retries

_HOST = "https://docs.google.com"
_CHUNK = 1024 * 1024  # 1 MB
_SSL = ssl.create_default_context(cafile=certifi.where())

# Matches a US-English "Month D, YYYY" date, e.g. "May 17, 2026".
_DATE = re.compile(r"[A-Z][a-z]+ \d{1,2}, \d{4}")


def _export_url(sheet_id: str, gid: str) -> str:
    qs = urllib.parse.urlencode({"format": "csv", "gid": gid})
    return f"{_HOST}/spreadsheets/d/{sheet_id}/export?{qs}"


def _cell_url(sheet_id: str, gid: str, cell: str) -> str:
    qs = urllib.parse.urlencode({"tqx": "out:csv", "gid": gid, "range": cell})
    return f"{_HOST}/spreadsheets/d/{sheet_id}/gviz/tq?{qs}"


def _read_cell(sheet_id: str, gid: str, cell: str) -> str:
    with urllib.request.urlopen(_cell_url(sheet_id, gid, cell), context=_SSL) as r:
        body = r.read().decode("utf-8")
    # The gviz CSV response wraps the single cell in standard CSV quoting.
    rows = list(csv.reader(io.StringIO(body)))
    if not rows or not rows[0]:
        raise RuntimeError(f"Google Sheet cell {cell} of {sheet_id} is empty")
    return rows[0][0]


def _version_from_cell(text: str, cell: str) -> str:
    """Return the last 'Month D, YYYY' date in `text` as YYYY-MM-DD."""
    matches = _DATE.findall(text)
    if not matches:
        raise RuntimeError(
            f"No 'Month D, YYYY' date found in cell {cell}: {text!r}"
        )
    return datetime.strptime(matches[-1], "%B %d, %Y").strftime("%Y-%m-%d")


def _download_csv(sheet_id: str, gid: str, name: str, dest: Path) -> Path:
    local_path = dest / name
    with urllib.request.urlopen(_export_url(sheet_id, gid), context=_SSL) as response:
        content_type = response.headers.get_content_type()
        if content_type != "text/csv":
            # A private or removed sheet redirects to an HTML sign-in page that
            # still returns 200; reject it rather than saving HTML as CSV.
            raise RuntimeError(
                f"{name}: expected text/csv from Google Sheets export, got "
                f"{content_type!r} (is the sheet public?)"
            )
        expected = int(response.headers.get("Content-Length", 0)) or None
        bytes_written = 0
        with (
            open(local_path, "wb") as f,
            tqdm(total=expected, unit="B", unit_scale=True, desc=name) as bar,
        ):
            while chunk := response.read(_CHUNK):
                f.write(chunk)
                bytes_written += len(chunk)
                bar.update(len(chunk))
    if expected is not None and bytes_written != expected:
        local_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"{name}: size mismatch — expected {expected} bytes, got {bytes_written}"
        )
    return local_path


class GoogleSheetDownloader(BaseDownloader):

    def latest_version(self, config: dict) -> str:
        cell = config.get("version_cell", "A1")
        text = _read_cell(config["sheet_id"], config["gid"], cell)
        return _version_from_cell(text, cell)

    def fetch(self, config: dict, dest: Path) -> list[Path]:
        sheet_id = config["sheet_id"]
        gid = config["gid"]
        name = config.get("filename", "sheet.csv")
        path = _with_retries(
            lambda: _download_csv(sheet_id, gid, name, dest)
        )
        return [path]
