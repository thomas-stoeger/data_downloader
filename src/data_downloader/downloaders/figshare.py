"""
Figshare collection downloader.

Fetches the most recent article from a Figshare collection and downloads
its files. Uses only stdlib (urllib) for API calls.

Required config keys:
    collection_id  — numeric Figshare collection ID

Optional config keys:
    files          — list of filenames to download (downloads all if omitted)
"""
import hashlib
import json
import re
import ssl
import urllib.request
from pathlib import Path

import certifi
from tqdm import tqdm

from .base import BaseDownloader, _with_retries

_API = "https://api.figshare.com/v2"
_CHUNK = 1024 * 1024  # 1 MB
_SSL = ssl.create_default_context(cafile=certifi.where())


def _get(url: str) -> dict | list:
    with urllib.request.urlopen(url, context=_SSL) as r:
        return json.loads(r.read())


def _latest_article(collection_id: str) -> dict:
    url = (
        f"{_API}/collections/{collection_id}/articles"
        f"?page_size=1&order=published_date&order_direction=desc"
    )
    articles = _get(url)
    if not articles:
        raise RuntimeError(f"No articles found in Figshare collection {collection_id}")
    return articles[0]


def _article_files(article_id: int) -> list[dict]:
    data = _get(f"{_API}/articles/{article_id}")
    return data["files"]


def _version_from_title(title: str) -> str:
    """Extract 'YYYY-MM' from titles like 'iCite Database Snapshot 2026-03'."""
    m = re.search(r"\d{4}-\d{2}", title)
    return m.group(0) if m else title


def _download_file(url: str, name: str, expected_size: int, expected_md5: str | None, dest: Path) -> Path:
    local_path = dest / name
    md5 = hashlib.md5()
    bytes_written = 0
    with urllib.request.urlopen(url, context=_SSL) as response:
        with (
            open(local_path, "wb") as f,
            tqdm(total=expected_size, unit="B", unit_scale=True, desc=name) as bar,
        ):
            while chunk := response.read(_CHUNK):
                f.write(chunk)
                md5.update(chunk)
                bytes_written += len(chunk)
                bar.update(len(chunk))
    if bytes_written != expected_size:
        local_path.unlink(missing_ok=True)
        raise RuntimeError(f"{name}: size mismatch — expected {expected_size} bytes, got {bytes_written}")
    if expected_md5 and md5.hexdigest() != expected_md5:
        local_path.unlink(missing_ok=True)
        raise RuntimeError(f"{name}: MD5 mismatch — expected {expected_md5}, got {md5.hexdigest()}")
    return local_path


class FigshareDownloader(BaseDownloader):

    def latest_version(self, config: dict) -> str:
        article = _latest_article(config["collection_id"])
        return _version_from_title(article["title"])

    def fetch(self, config: dict, dest: Path) -> list[Path]:
        collection_id = config["collection_id"]
        wanted: list[str] | None = config.get("files")

        article = _latest_article(collection_id)
        article_id = article["id"]
        files = _article_files(article_id)

        if wanted:
            files = [f for f in files if f["name"] in wanted]
            if not files:
                raise RuntimeError(
                    f"None of the requested files {wanted} found in article {article_id}. "
                    f"Available: {[f['name'] for f in _article_files(article_id)]}"
                )

        written = []
        for file_info in files:
            name = file_info["name"]
            expected_size = file_info["size"]
            expected_md5 = file_info.get("computed_md5")
            url = file_info["download_url"]
            local_path = dest / name

            written.append(_with_retries(lambda u=url, n=name, s=expected_size, m=expected_md5, d=dest: _download_file(u, n, s, m, d)))

        return written
