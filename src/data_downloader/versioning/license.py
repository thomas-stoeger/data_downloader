"""
Per-version license capture and drift detection.

Each dataset may declare a license in its registry config under a nested
`[<name>.license]` table. This module captures, for a given version snapshot,
two things the user later needs to answer "what is this dataset's license and
where did it come from":

  a) a *provenance reference* (`declared_at`) — the upstream page/URL where the
     license is stated. Recorded verbatim, never fetched.
  b) a *byte-exact copy* of the license document(s) (`url`) — downloaded into
     the version directory and hashed, so the exact terms that governed this
     snapshot are preserved and changes are detectable.

License config keys (all optional):
    declared_at  - str or list[str]: where the license is declared upstream.
    url          - str or list[str]: license document(s) to download and hash.
    spdx         - str: SPDX identifier, recorded as documentation.
    note         - str: free-form note, recorded as documentation.

Capture is best-effort: a failed license download must never discard an
otherwise-verified data snapshot (see versioning/store.py). The fetched bytes,
URLs, and filenames are untrusted data — filenames are reduced to a sanitized
basename and used only to write into the version directory, never interpreted.
"""
import hashlib
import ssl
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import certifi

_CHUNK = 1024 * 1024  # 1 MB
_SSL = ssl.create_default_context(cafile=certifi.where())
# Mirror obo.py: some hosts reject the default urllib User-Agent with 403.
_HEADERS = {"User-Agent": "data_downloader (+https://github.com/)"}

# Subdirectory inside a version directory that holds captured license copies.
LICENSE_DIR = "_license"


def _as_list(value) -> list[str]:
    """Normalize a config value that may be a string, a list, or absent."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers=_HEADERS)


def _safe_name(url: str) -> str:
    """Derive a filesystem-safe filename from a URL. The URL is untrusted, so
    take only the basename and fall back to a generic name."""
    name = Path(urllib.parse.urlparse(url).path).name
    # Strip any path separators that survived and reject empty/odd names.
    name = name.replace("/", "_").replace("\\", "_").strip()
    return name or "license.txt"


def _fetch_bytes(url: str) -> bytes:
    buf = bytearray()
    with urllib.request.urlopen(_request(url), context=_SSL) as response:
        expected = int(response.headers.get("Content-Length", 0)) or None
        while chunk := response.read(_CHUNK):
            buf += chunk
    if expected is not None and len(buf) != expected:
        raise RuntimeError(
            f"license {url}: size mismatch — expected {expected} bytes, got {len(buf)}"
        )
    return bytes(buf)


def _common(license_cfg: dict) -> dict:
    """Documentation fields shared by every record shape."""
    record: dict = {}
    declared = _as_list(license_cfg.get("declared_at"))
    if declared:
        record["declared_at"] = declared
    if license_cfg.get("spdx"):
        record["spdx"] = license_cfg["spdx"]
    if license_cfg.get("note"):
        record["note"] = license_cfg["note"]
    return record


def capture_license(license_cfg: dict, version_dir: Path) -> dict:
    """Download the license document(s) into version_dir/_license/, hash them,
    and return a record describing the provenance and the stored copies.

    Raises on any download failure; the caller (store) catches and records the
    failure so a license problem never discards the data snapshot.
    """
    record = _common(license_cfg)
    record["retrieved_at"] = datetime.now(timezone.utc).isoformat()

    urls = _as_list(license_cfg.get("url"))
    documents: list[dict] = []
    if urls:
        license_subdir = version_dir / LICENSE_DIR
        license_subdir.mkdir(parents=True, exist_ok=True)
        for url in urls:
            data = _fetch_bytes(url)
            if not data:
                raise RuntimeError(f"license {url}: empty after download")
            name = _safe_name(url)
            local_path = license_subdir / name
            local_path.write_bytes(data)
            documents.append(
                {
                    "url": url,
                    "file": str(local_path.relative_to(version_dir.parent)),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "bytes": len(data),
                }
            )
    record["documents"] = documents
    return record


def license_fingerprint(license_cfg: dict) -> dict[str, str]:
    """Fetch each license URL into memory and return {url: sha256}, without
    writing to disk. Used by `check` to compare against the recorded hashes."""
    return {
        url: hashlib.sha256(_fetch_bytes(url)).hexdigest()
        for url in _as_list(license_cfg.get("url"))
    }


def _recorded_hashes(prev_record: dict | None) -> dict[str, str]:
    """Extract {url: sha256} from a previously recorded license record."""
    if not prev_record:
        return {}
    return {
        doc["url"]: doc["sha256"]
        for doc in prev_record.get("documents", [])
        if "url" in doc and "sha256" in doc
    }


def has_changed(prev_record: dict | None, new_hashes: dict[str, str]) -> bool:
    """True if the new per-URL hashes differ from those in prev_record, or a
    document appeared or disappeared. A missing previous record with new docs
    counts as new, not changed (callers distinguish 'new' from 'changed')."""
    prev = _recorded_hashes(prev_record)
    if not prev:
        return False
    return prev != new_hashes
