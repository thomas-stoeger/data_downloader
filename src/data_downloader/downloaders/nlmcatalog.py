"""
NLM Catalog downloader.

Fetches NLM Catalog records matching a search term via NCBI E-utilities
(esearch + efetch with WebEnv history) and stitches all batches into a
single XML file per snapshot. This is the same mechanism behind the
NLM Catalog UI's "Send to > File > XML" button, just bulk and resumable.

Batches are concatenated by byte-slicing the `<NLMCatalogRecordSet>`
wrapper so the assembled file keeps the upstream DOCTYPE and exact record
formatting that downstream parsers expect.

NLM Catalog publishes no stable release marker, so the version string is
today's date — like nih_exporter, `check` always reports an update
available and `fetch` re-downloads if run on a new day.

Required config keys:
    term  - the NCBI Entrez search term, e.g. "reportedmedline"
"""
import re
import ssl
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

import certifi
from tqdm import tqdm

from .base import BaseDownloader, _with_retries

_EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
_DB = "nlmcatalog"
_BATCH = 500
# NCBI requests <= 3 URL requests per second without an API key.
_REQUEST_INTERVAL = 0.34
_SSL = ssl.create_default_context(cafile=certifi.where())

_OPEN_TAG = re.compile(rb"<NLMCatalogRecordSet\b[^>]*>")
_CLOSE_TAG = b"</NLMCatalogRecordSet>"
_RECORD_OPEN = re.compile(rb"<NLMCatalogRecord\b")


def _get(url: str) -> bytes:
    with urllib.request.urlopen(url, context=_SSL) as r:
        return r.read()


def _esearch_history(term: str) -> tuple[int, str, str]:
    """Run esearch with usehistory=y. Returns (count, query_key, webenv)."""
    qs = urllib.parse.urlencode({
        "db": _DB, "term": term, "usehistory": "y", "retmax": 0,
    })
    body = _get(f"{_EUTILS}/esearch.fcgi?{qs}")
    root = ET.fromstring(body)
    count_text = root.findtext("Count")
    qk = root.findtext("QueryKey")
    we = root.findtext("WebEnv")
    if count_text is None or qk is None or we is None:
        raise RuntimeError(
            f"NLM Catalog esearch response missing Count/QueryKey/WebEnv: {body[:500]!r}"
        )
    return int(count_text), qk, we


def _efetch(query_key: str, webenv: str, retstart: int, retmax: int) -> bytes:
    qs = urllib.parse.urlencode({
        "db": _DB,
        "query_key": query_key,
        "WebEnv": webenv,
        "retmode": "xml",
        "retstart": retstart,
        "retmax": retmax,
    })
    return _get(f"{_EUTILS}/efetch.fcgi?{qs}")


def _split_batch(batch: bytes) -> tuple[bytes, bytes]:
    """Return (preamble_through_opening_tag, inner_records_bytes)."""
    m = _OPEN_TAG.search(batch)
    if not m:
        raise RuntimeError(
            f"efetch response missing <NLMCatalogRecordSet> opening tag: {batch[:500]!r}"
        )
    close_idx = batch.rfind(_CLOSE_TAG)
    if close_idx == -1:
        raise RuntimeError("efetch response missing </NLMCatalogRecordSet> closing tag")
    return batch[: m.end()], batch[m.end() : close_idx]


class NlmCatalogDownloader(BaseDownloader):

    def latest_version(self, _: dict) -> str:
        return date.today().strftime("%Y-%m-%d")

    def fetch(self, config: dict, dest: Path) -> list[Path]:
        term = config["term"]
        count, query_key, webenv = _esearch_history(term)
        if count == 0:
            raise RuntimeError(
                f"NLM Catalog esearch returned 0 records for term {term!r}"
            )

        batches = list(range(0, count, _BATCH))
        print(
            f"  esearch matched {count} records, fetching {len(batches)} "
            f"batches of up to {_BATCH}..."
        )

        out = dest / f"nlmcatalog_{term}.xml"
        record_count = 0

        with open(out, "wb") as f:
            for i, start in enumerate(
                tqdm(batches, desc=f"nlmcatalog/{term}", unit="batch")
            ):
                retmax = min(_BATCH, count - start)
                batch = _with_retries(
                    lambda s=start, m=retmax: _efetch(query_key, webenv, s, m)
                )

                if i == 0:
                    # Parse the first batch so a malformed/error response from
                    # eutils fails fast rather than after all batches are stitched.
                    ET.fromstring(batch)

                preamble, inner = _split_batch(batch)
                if i == 0:
                    f.write(preamble)
                f.write(inner)
                record_count += len(_RECORD_OPEN.findall(inner))

                if i + 1 < len(batches):
                    time.sleep(_REQUEST_INTERVAL)

            f.write(_CLOSE_TAG)
            f.write(b"\n")

        if record_count != count:
            raise RuntimeError(
                f"NLM Catalog: expected {count} records but stitched "
                f"{record_count} from {len(batches)} batches"
            )

        return [out]
