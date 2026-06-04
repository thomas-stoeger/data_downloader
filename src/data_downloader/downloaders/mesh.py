"""
MeSH (Medical Subject Headings) downloader.

The U.S. National Library of Medicine publishes MeSH once per production year.
As of January 2026 the legacy ASCII serialization (the old `.bin` and `mtrees`
files) is discontinued; the current bulk formats are XML, MARC 21, and RDF.
This downloader fetches the XML record sets, served from:

    https://nlmpubs.nlm.nih.gov/projects/mesh/MESH_FILES/xmlmesh/

The directory holds, per production year (e.g. 2026):

    desc<year>.xml / desc<year>.gz   descriptors  (carry the MeSH tree numbers)
    qual<year>.xml                   qualifiers
    pa<year>.xml                     pharmacological actions
    supp<year>.xml / supp<year>.gz   supplementary concept records (SCR, large)

Descriptors are the record set that carries each heading's `<TreeNumberList>`,
so the descriptor file is what supports parent/ancestor lookups: a term's
ancestors are the prefixes of its dotted tree number (e.g. `C01.539.800` has
parents `C01.539` and `C01`).

Versioning: the version string is the MeSH production year (e.g. `2026`), read
from the directory listing without downloading any data. MeSH descriptors and
qualifiers are refreshed annually; the SCR file is refreshed on weekdays, but
this downloader tracks the annual production year and does not chase daily SCR
updates.

Config keys:
    record_sets (optional) list of record-set prefixes to download, from
                "desc", "qual", "pa", "supp". Use ["*"] for all four. Defaults
                to all four.
    compressed  (optional) when true (default), prefer the gzipped form where
                the server publishes one (desc, supp); qualifiers and
                pharmacological actions are only offered as plain .xml and are
                always fetched as such. Set false to always fetch the .xml.
"""
import re
import ssl
import urllib.request
from pathlib import Path

import certifi
from tqdm import tqdm

from .base import BaseDownloader, _with_retries

_BASE = "https://nlmpubs.nlm.nih.gov/projects/mesh/MESH_FILES/xmlmesh"
_CHUNK = 1024 * 1024  # 1 MB
_SSL = ssl.create_default_context(cafile=certifi.where())
# Identify the tool; the default Python-urllib user agent is best avoided.
_HEADERS = {"User-Agent": "data_downloader (+https://www.nlm.nih.gov)"}

# The MeSH XML record sets, by the filename prefix the server uses.
_RECORD_SETS = ("desc", "qual", "pa", "supp")

_HREF_RE = re.compile(r'href="([^"]+)"')
_DESC_YEAR_RE = re.compile(r"^desc(\d{4})\.(?:xml|gz)$")


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers=_HEADERS)


def _list_dir(url: str) -> list[str]:
    """Return the file/dir entry names in an Apache autoindex listing.

    Column-sort links and the parent/absolute links the autoindex emits are
    dropped; directory names keep their trailing slash.
    """
    with urllib.request.urlopen(_request(url), context=_SSL) as r:
        html = r.read().decode("utf-8", errors="replace")
    names: list[str] = []
    for href in _HREF_RE.findall(html):
        if href.startswith("?") or href.startswith("/") or href == "../":
            continue
        names.append(href)
    return names


def _latest_version(entries: list[str] | None = None) -> str:
    """Return the latest MeSH production year (e.g. "2026") from the listing."""
    entries = entries if entries is not None else _list_dir(f"{_BASE}/")
    years = {m.group(1) for n in entries if (m := _DESC_YEAR_RE.match(n))}
    if not years:
        raise RuntimeError(f"No descYYYY MeSH files found under {_BASE}")
    return max(years)


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


class MeshDownloader(BaseDownloader):

    def latest_version(self, config: dict) -> str:
        return _latest_version()

    def fetch(self, config: dict, dest: Path) -> list[Path]:
        # The store names the version directory after latest_version(), so the
        # production year is the directory name; reuse it.
        version = dest.name
        compressed = config.get("compressed", True)

        requested = config.get("record_sets") or list(_RECORD_SETS)
        if requested == ["*"]:
            selected = list(_RECORD_SETS)
        else:
            unknown = [r for r in requested if r not in _RECORD_SETS]
            if unknown:
                raise RuntimeError(
                    f"mesh: unknown record set(s) {unknown}. "
                    f"Available: {', '.join(_RECORD_SETS)}"
                )
            selected = list(requested)

        entries = _list_dir(f"{_BASE}/")

        written: list[Path] = []
        for prefix in selected:
            name = self._pick_file(prefix, version, compressed, entries)
            url = f"{_BASE}/{name}"
            written.append(
                _with_retries(lambda u=url, n=name: _download_file(u, dest / n))
            )
        return written

    @staticmethod
    def _pick_file(
        prefix: str, version: str, compressed: bool, entries: list[str]
    ) -> str:
        """Choose the file to download for a record set: prefer the gzipped form
        when requested and available, otherwise the plain .xml."""
        gz = f"{prefix}{version}.gz"
        xml = f"{prefix}{version}.xml"
        if compressed and gz in entries:
            return gz
        if xml in entries:
            return xml
        if gz in entries:
            return gz
        raise RuntimeError(
            f"mesh: no {prefix}{version}.(xml|gz) found under {_BASE}. "
            f"Present: {', '.join(sorted(entries))}"
        )
