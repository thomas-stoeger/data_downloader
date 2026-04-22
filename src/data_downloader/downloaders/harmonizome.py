"""
Harmonizome gene-to-attribute matrix downloader.

Discovers datasets by scraping https://maayanlab.cloud/Harmonizome/download,
then visits each dataset page and extracts the gene-attribute matrix download
URL from the embedded JSON-LD structured data.  Only datasets that publish a
"gene-attribute matrix" file are downloaded; others are silently skipped.

No config keys required.
"""
import json
import re
import ssl
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

import certifi
from tqdm import tqdm

from .base import BaseDownloader, _with_retries

_BASE_URL = "https://maayanlab.cloud/Harmonizome"
_CHUNK = 1024 * 1024  # 1 MB
_SSL = ssl.create_default_context(cafile=certifi.where())

_GENE_ATTR_NAME = "gene-attribute matrix"


def _get(url: str) -> str:
    with urllib.request.urlopen(url, context=_SSL) as r:
        return r.read().decode("utf-8", errors="replace")


class _DatasetLinkParser(HTMLParser):
    """Collect all hrefs matching /dataset/... from the download page table."""

    def __init__(self) -> None:
        super().__init__()
        self.dataset_hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href", "")
        if href and re.match(r"^dataset/", href):
            self.dataset_hrefs.append(href)


def _scrape_dataset_hrefs() -> list[str]:
    html = _get(f"{_BASE_URL}/download")
    parser = _DatasetLinkParser()
    parser.feed(html)
    return parser.dataset_hrefs


def _gene_attr_url(dataset_href: str) -> str | None:
    """Return the gene-attribute matrix URL from a dataset page, or None."""
    url = f"{_BASE_URL}/{dataset_href}"
    try:
        html = _get(url)
    except Exception:
        return None

    for match in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        re.DOTALL,
    ):
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        for item in data.get("distribution", []):
            if item.get("name", "").strip().lower() == _GENE_ATTR_NAME:
                return item.get("contentUrl")
    return None


def _last_modified(url: str) -> str | None:
    """Return Last-Modified of url as 'YYYY-MM-DD', or None if unavailable."""
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, context=_SSL) as resp:
            if resp.status != 200:
                return None
            lm = resp.headers.get("Last-Modified", "")
            return parsedate_to_datetime(lm).strftime("%Y-%m-%d") if lm else None
    except Exception:
        return None


def _filename_from_url(url: str, dataset_href: str) -> str:
    """Build a local filename: {dataset_slug}__gene_attribute_matrix.txt.gz"""
    # Extract the data path segment from the URL
    # e.g. .../harmonizome/data/achilles/gene_attribute_matrix.txt.gz -> achilles
    m = re.search(r"/harmonizome/data/([^/]+)/", url)
    slug = m.group(1) if m else dataset_href.split("/")[-1].replace("+", "_")
    return f"{slug}__gene_attribute_matrix.txt.gz"


def _download_file(url: str, filename: str, size: int, dest: Path) -> Path:
    local_path = dest / filename
    label = filename.split("__")[0]
    bytes_written = 0
    with urllib.request.urlopen(url, context=_SSL) as response:
        with (
            open(local_path, "wb") as f,
            tqdm(total=size or None, unit="B", unit_scale=True, desc=label) as bar,
        ):
            while chunk := response.read(_CHUNK):
                f.write(chunk)
                bytes_written += len(chunk)
                bar.update(len(chunk))
    if size and bytes_written != size:
        local_path.unlink(missing_ok=True)
        raise RuntimeError(f"{filename}: size mismatch — expected {size} bytes, got {bytes_written}")
    return local_path


class HarmonizomeDownloader(BaseDownloader):

    def latest_version(self, _: dict) -> str:
        """Return the most recent Last-Modified date across all available gene-attribute matrices."""
        hrefs = _scrape_dataset_hrefs()

        def resolve(href: str) -> str | None:
            url = _gene_attr_url(href)
            return _last_modified(url) if url else None

        with ThreadPoolExecutor(max_workers=6) as pool:
            dates = list(pool.map(resolve, hrefs))

        valid = [d for d in dates if d]
        if not valid:
            raise RuntimeError("Could not determine Last-Modified for any Harmonizome dataset")
        return max(valid)

    def fetch(self, _: dict, dest: Path) -> list[Path]:
        print("Scraping dataset list from Harmonizome download page...")
        hrefs = _scrape_dataset_hrefs()
        print(f"Found {len(hrefs)} datasets. Resolving gene-attribute matrix URLs...")

        written: list[Path] = []
        for href in hrefs:
            download_url = _gene_attr_url(href)
            if not download_url:
                continue

            filename = _filename_from_url(download_url, href)
            local_path = dest / filename

            # HEAD to get size
            try:
                req = urllib.request.Request(download_url, method="HEAD")
                with urllib.request.urlopen(req, context=_SSL) as resp:
                    if resp.status != 200:
                        continue
                    size = int(resp.headers.get("Content-Length", 0))
            except urllib.error.HTTPError:
                continue

            written.append(_with_retries(lambda u=download_url, f=filename, s=size, d=dest: _download_file(u, f, s, d)))

        return written
