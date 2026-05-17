"""
Harmonizome downloader.

Discovers datasets by scraping https://maayanlab.cloud/Harmonizome/download.
For each dataset, parses the JSON-LD `distribution` list plus a few HTML
table rows for metadata, then downloads every distribution file except the
gene/attribute similarity matrices (which are large and rarely needed in
downstream ETL — they can be recomputed from the gene-attribute matrix).
A JSON metadata sidecar named `{slug}__metadata.json` is written per
dataset, capturing description, category, measurement, association,
resource, citations, stats, and the list of files actually downloaded.

No config keys required.
"""
import json
import re
import ssl
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

import certifi
from tqdm import tqdm

from .base import BaseDownloader
from ..versioning.integrity import verify_file

_BASE_URL = "https://maayanlab.cloud/Harmonizome"
_CHUNK = 1024 * 1024  # 1 MB
_SSL = ssl.create_default_context(cafile=certifi.where())

# Distribution `name` values to skip. Cosine-similarity matrices are large
# and can be recomputed from the gene-attribute matrix downstream.
_EXCLUDED_FILE_TYPES = frozenset({
    "gene similarity matrix",
    "attribute similarity matrix",
})

# Every dataset publishes a "gene-attribute matrix"; its Last-Modified is
# treated as a cheap proxy for whole-dataset freshness during `dl check`.
_VERSION_FILE_TYPE = "gene-attribute matrix"

# HTML table rows captured into the metadata sidecar (label -> attr name).
# Description, category (JSON-LD `keywords`), and citation come from JSON-LD.
_HTML_FIELDS = {
    "Measurement": "measurement",
    "Association": "association",
    "Resource": "resource",
    "Last Updated": "last_updated",
}


@dataclass
class _Distribution:
    name: str
    description: str
    content_url: str


@dataclass
class _Dataset:
    href: str
    page_url: str
    name: str
    slug: str
    description: str | None = None
    category: str | None = None
    citations: list[str] = field(default_factory=list)
    measurement: str | None = None
    association: str | None = None
    resource: str | None = None
    last_updated: str | None = None
    stats: list[str] = field(default_factory=list)
    distributions: list[_Distribution] = field(default_factory=list)

    def distribution_by_name(self, name: str) -> _Distribution | None:
        for d in self.distributions:
            if d.name == name:
                return d
        return None


def _get(url: str) -> str:
    with urllib.request.urlopen(url, context=_SSL) as r:
        return r.read().decode("utf-8", errors="replace")


class _DatasetLinkParser(HTMLParser):
    """Collect hrefs of the form `dataset/...` from the download page."""

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
    parser = _DatasetLinkParser()
    parser.feed(_get(f"{_BASE_URL}/download"))
    seen: set[str] = set()
    out: list[str] = []
    for h in parser.dataset_hrefs:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out


def _strip_html(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _slug_from_url(url: str, fallback: str) -> str:
    m = re.search(r"/harmonizome/data/([^/]+)/", url)
    return m.group(1) if m else fallback.replace("+", "_")


def _parse_dataset_page(href: str) -> _Dataset | None:
    page_url = f"{_BASE_URL}/{href}"
    try:
        html = _get(page_url)
    except Exception:
        return None

    jsonld = None
    for m in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL,
    ):
        try:
            jsonld = json.loads(m.group(1))
            break
        except json.JSONDecodeError:
            continue
    if not jsonld:
        return None

    distributions: list[_Distribution] = []
    for item in jsonld.get("distribution", []):
        name = (item.get("name") or "").strip()
        url = item.get("contentUrl") or ""
        if not name or not url:
            continue
        distributions.append(_Distribution(
            name=name,
            description=(item.get("description") or "").strip(),
            content_url=url,
        ))

    href_tail = href.split("/", 1)[-1]
    slug = (
        _slug_from_url(distributions[0].content_url, href_tail)
        if distributions
        else href_tail.replace("+", "_")
    )

    raw_keywords = jsonld.get("keywords")
    if isinstance(raw_keywords, list):
        category = ", ".join(str(k).strip() for k in raw_keywords if str(k).strip()) or None
    elif isinstance(raw_keywords, str):
        category = raw_keywords.strip() or None
    else:
        category = None

    raw_citation = jsonld.get("citation")
    if isinstance(raw_citation, list):
        citations = [str(c).strip() for c in raw_citation if str(c).strip()]
    elif isinstance(raw_citation, str):
        citations = [raw_citation.strip()] if raw_citation.strip() else []
    else:
        citations = []

    ds = _Dataset(
        href=href,
        page_url=page_url,
        name=str(jsonld.get("name") or slug).strip(),
        slug=slug,
        description=(jsonld.get("description") or "").strip() or None,
        category=category,
        citations=citations,
        distributions=distributions,
    )

    for label, key in _HTML_FIELDS.items():
        m = re.search(
            r'<td class="col-md-2">\s*' + re.escape(label) + r'\s*</td>\s*'
            r'<td class="col-md-10[^"]*">(.*?)</td>',
            html, re.DOTALL,
        )
        if m:
            value = _strip_html(m.group(1))
            setattr(ds, key, value or None)

    m = re.search(
        r'<td class="col-md-2">\s*Stats\s*</td>\s*'
        r'<td class="col-md-10[^"]*">(.*?)</td>',
        html, re.DOTALL,
    )
    if m:
        ds.stats = [
            _strip_html(li)
            for li in re.findall(r"<li>(.*?)</li>", m.group(1), re.DOTALL)
            if _strip_html(li)
        ]

    return ds


def _discover_datasets() -> list[_Dataset]:
    hrefs = _scrape_dataset_hrefs()
    with ThreadPoolExecutor(max_workers=6) as pool:
        parsed = list(pool.map(_parse_dataset_page, hrefs))
    return [d for d in parsed if d is not None]


def _last_modified(url: str) -> str | None:
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, context=_SSL) as resp:
            if resp.status != 200:
                return None
            lm = resp.headers.get("Last-Modified", "")
            return parsedate_to_datetime(lm).strftime("%Y-%m-%d") if lm else None
    except Exception:
        return None


def _head_content_length(url: str) -> int | None:
    """Content-Length, or None if the URL isn't fetchable or is zero bytes.

    Harmonizome occasionally serves intentionally empty distributions (e.g. DisGeNET
    gene-attribute matrices, presumably to respect upstream redistribution policies)
    and other distributions may simply not be on S3. Both cases return None here so
    the fetch loop skips them rather than writing empty/missing files that would
    later fail the gzip integrity check and trigger a full version rmtree.
    """
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, context=_SSL) as resp:
            if resp.status != 200:
                return None
            length = int(resp.headers.get("Content-Length", 0))
            return length or None
    except urllib.error.HTTPError:
        return None


def _download_file(url: str, local_path: Path, size: int) -> Path:
    label = local_path.name.split("__", 1)[0]
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
        raise RuntimeError(
            f"{local_path.name}: size mismatch — expected {size} bytes, got {bytes_written}"
        )
    return local_path


_VERIFY_ATTEMPTS = 3


def _download_with_verify(
    url: str, local_path: Path, size: int, dest: Path
) -> tuple[Path | None, str | None]:
    """Download + verify with retry. Returns (verified_path, None) on success,
    or (None, error_reason) after all attempts fail. On persistent failure the
    last downloaded copy is moved to `dest/failed/<filename>` for inspection.
    """
    last_error: str | None = None
    for attempt in range(_VERIFY_ATTEMPTS):
        try:
            _download_file(url, local_path, size)
            verify_file(local_path)
            return local_path, None
        except Exception as exc:
            last_error = str(exc)
            print(
                f"  attempt {attempt + 1}/{_VERIFY_ATTEMPTS} failed for "
                f"{local_path.name}: {exc}"
            )
            if attempt < _VERIFY_ATTEMPTS - 1 and local_path.exists():
                local_path.unlink(missing_ok=True)

    if local_path.exists():
        failed_dir = dest / "failed"
        failed_dir.mkdir(parents=True, exist_ok=True)
        local_path.rename(failed_dir / local_path.name)
    return None, last_error


def _local_filename(slug: str, content_url: str) -> str:
    return f"{slug}__{content_url.rsplit('/', 1)[-1]}"


def _write_metadata(
    dataset: _Dataset,
    dest: Path,
    downloaded: list[tuple[str, str]],
    failed: list[tuple[str, str, str]],
) -> Path:
    payload = {
        "name": dataset.name,
        "slug": dataset.slug,
        "page_url": dataset.page_url,
        "description": dataset.description,
        "category": dataset.category,
        "measurement": dataset.measurement,
        "association": dataset.association,
        "resource": dataset.resource,
        "last_updated": dataset.last_updated,
        "stats": dataset.stats,
        "citations": dataset.citations,
        "files": [
            {"file_type": ft, "filename": fn} for ft, fn in downloaded
        ],
        "failed_files": [
            {"file_type": ft, "filename": fn, "reason": reason}
            for ft, fn, reason in failed
        ],
    }
    path = dest / f"{dataset.slug}__metadata.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    return path


class HarmonizomeDownloader(BaseDownloader):

    def latest_version(self, _: dict) -> str:
        datasets = _discover_datasets()

        def date_for(d: _Dataset) -> str | None:
            dist = d.distribution_by_name(_VERSION_FILE_TYPE)
            return _last_modified(dist.content_url) if dist else None

        with ThreadPoolExecutor(max_workers=6) as pool:
            dates = list(pool.map(date_for, datasets))

        valid = [x for x in dates if x]
        if not valid:
            raise RuntimeError("Could not determine Last-Modified for any Harmonizome dataset")
        return max(valid)

    def fetch(self, _: dict, dest: Path) -> list[Path]:
        print("Scraping dataset list from Harmonizome download page...")
        datasets = _discover_datasets()
        print(
            f"Found {len(datasets)} datasets. Downloading all distributions "
            f"except similarity matrices, plus metadata sidecars..."
        )

        written: list[Path] = []
        total_failed = 0
        for dataset in datasets:
            downloaded_for_dataset: list[tuple[str, str]] = []
            failed_for_dataset: list[tuple[str, str, str]] = []
            for dist in dataset.distributions:
                if dist.name in _EXCLUDED_FILE_TYPES:
                    continue
                size = _head_content_length(dist.content_url)
                if size is None:
                    continue
                local_path = dest / _local_filename(dataset.slug, dist.content_url)
                verified, error = _download_with_verify(
                    dist.content_url, local_path, size, dest
                )
                if verified is not None:
                    written.append(verified)
                    downloaded_for_dataset.append((dist.name, verified.name))
                else:
                    failed_for_dataset.append(
                        (dist.name, local_path.name, error or "unknown")
                    )

            if downloaded_for_dataset or failed_for_dataset:
                written.append(
                    _write_metadata(
                        dataset, dest, downloaded_for_dataset, failed_for_dataset
                    )
                )
            total_failed += len(failed_for_dataset)

        if total_failed:
            print(
                f"Quarantined {total_failed} file(s) to {dest / 'failed'} — "
                f"see each dataset's metadata sidecar `failed_files` for reasons."
            )

        return written
