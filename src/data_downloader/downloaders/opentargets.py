"""
Open Targets Platform downloader.

The Open Targets Platform (https://platform.opentargets.org) publishes each
release as a set of Parquet datasets on the EBI FTP server, served over HTTPS:

    https://ftp.ebi.ac.uk/pub/databases/opentargets/platform/<version>/output/<dataset>/

Releases are versioned YY.MM (e.g. 26.03). Each dataset directory is a Spark
output: a zero-byte _SUCCESS marker plus many part-*.snappy.parquet files.
Output is Parquet only (Open Targets deprecated its JSON outputs).

Alongside the data this downloader captures, for the whole release:

  - croissant.json              the Croissant (JSON-LD) metadata, which carries
                                the field-level schema for every dataset.
  - release_data_integrity.sha1 per-file SHA1 checksums, used here to verify
                                every Parquet file after download.
  - manifest.json               the release manifest (provenance).
  - downloads.json              the payload behind the platform's Downloads page
                                (the GraphQL `meta.downloads` field), Croissant
                                JSON-LD that holds the human-readable text
                                description shown for each dataset. Fetched from
                                the Open Targets API.

These release-level files cover the schema and the description for every
dataset, so they are always captured regardless of which datasets' Parquet
bytes are selected below.

Versioning: the version string is the release tag (YY.MM), read cheaply from
the FTP listing without downloading anything.

Config keys:
    datasets  (required) list of output dataset names to download, e.g.
              ["target", "disease", "evidence"]. Use ["*"] to fetch every
              dataset in the release (very large, 100+ GB). The schema
              (croissant.json) and descriptions (downloads.json) for the whole
              release are captured regardless of this selection.
    version   (optional) pin a specific release tag (e.g. "25.06"). Defaults to
              the latest YY.MM release on the FTP server.
"""
import hashlib
import json
import re
import ssl
import urllib.request
from pathlib import Path

import certifi
from tqdm import tqdm

from .base import BaseDownloader, _with_retries, is_doc_filename

_FTP_BASE = "https://ftp.ebi.ac.uk/pub/databases/opentargets/platform"
_API = "https://api.platform.opentargets.org/api/v4/graphql"
_CHUNK = 1024 * 1024  # 1 MB
_SSL = ssl.create_default_context(cafile=certifi.where())

# Release-level files always fetched alongside the data. croissant.json carries
# the per-dataset schema; the .sha1 file carries the checksums used to verify
# every downloaded Parquet file; manifest.json records release provenance.
_RELEASE_FILES = ("croissant.json", "release_data_integrity.sha1", "manifest.json")

_VERSION_RE = re.compile(r"^\d{2}\.\d{2}$")
_HREF_RE = re.compile(r'href="([^"]+)"')
_SHA1_RE = re.compile(r"^[0-9a-fA-F]{40}$")


def _list_dir(url: str) -> list[str]:
    """Return the entry names in an EBI FTP-over-HTTPS autoindex directory.

    Directory names keep their trailing slash; the column-sort links and the
    parent/absolute links the autoindex emits are dropped.
    """
    with urllib.request.urlopen(url, context=_SSL) as r:
        html = r.read().decode("utf-8", errors="replace")
    names: list[str] = []
    for href in _HREF_RE.findall(html):
        if href.startswith("?") or href.startswith("/") or href == "../":
            continue
        names.append(href)
    return names


def _latest_version() -> str:
    tags = [
        n.rstrip("/") for n in _list_dir(f"{_FTP_BASE}/")
        if _VERSION_RE.match(n.rstrip("/"))
    ]
    if not tags:
        raise RuntimeError(f"No YY.MM release directories found under {_FTP_BASE}")
    return max(tags, key=lambda t: tuple(int(p) for p in t.split(".")))


def _graphql_downloads() -> str:
    """Return the platform Downloads page payload (the `meta.downloads` JSON
    string), which contains the text description shown for each dataset.

    The value is itself a JSON string; it is validated here and returned
    verbatim so the saved sidecar matches exactly what the page consumes.
    """
    body = json.dumps({"query": "{ meta { downloads } }"}).encode("utf-8")
    req = urllib.request.Request(
        _API, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, context=_SSL) as r:
        payload = json.loads(r.read())
    downloads = payload.get("data", {}).get("meta", {}).get("downloads")
    if not downloads:
        raise RuntimeError("Open Targets API returned no meta.downloads payload")
    json.loads(downloads)  # validate it is parseable JSON before saving
    return downloads


def _parse_sha1(text: str) -> dict[str, str]:
    """Parse a `<sha1>  <path>` checksum file into {basename: sha1}.

    Keys are file basenames so a checksum matches regardless of the path prefix
    used in the file (the parts land in per-dataset subdirectories locally).
    """
    out: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2 or not _SHA1_RE.match(parts[0]):
            continue
        basename = Path(parts[-1].lstrip("*./")).name
        out[basename] = parts[0].lower()
    return out


def _download_file(url: str, local_path: Path, expected_sha1: str | None) -> Path:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    sha1 = hashlib.sha1()
    bytes_written = 0
    with urllib.request.urlopen(url, context=_SSL) as response:
        expected_size = int(response.headers.get("Content-Length", 0)) or None
        with (
            open(local_path, "wb") as f,
            tqdm(total=expected_size, unit="B", unit_scale=True, desc=local_path.name) as bar,
        ):
            while chunk := response.read(_CHUNK):
                f.write(chunk)
                sha1.update(chunk)
                bytes_written += len(chunk)
                bar.update(len(chunk))
    if expected_size is not None and bytes_written != expected_size:
        local_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"{local_path.name}: size mismatch, expected {expected_size} bytes, got {bytes_written}"
        )
    if expected_sha1 and sha1.hexdigest() != expected_sha1:
        local_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"{local_path.name}: SHA1 mismatch, expected {expected_sha1}, got {sha1.hexdigest()}"
        )
    return local_path


class OpenTargetsDownloader(BaseDownloader):

    def latest_version(self, config: dict) -> str:
        return config.get("version") or _latest_version()

    def fetch(self, config: dict, dest: Path) -> list[Path]:
        # The store names the version directory after latest_version(), so the
        # release tag is the directory name. This keeps fetch consistent with
        # the version that was checked, without a second listing request.
        version = dest.name
        requested = config.get("datasets")
        release_url = f"{_FTP_BASE}/{version}"
        output_url = f"{release_url}/output"

        available = sorted(
            n.rstrip("/") for n in _list_dir(f"{output_url}/") if n.endswith("/")
        )

        if not requested:
            raise RuntimeError(
                "opentargets: set 'datasets' in the registry to choose which "
                'datasets to download (use ["*"] for the entire release). '
                f"Available in {version}: {', '.join(available)}"
            )

        if requested == ["*"]:
            selected = available
        else:
            missing = [d for d in requested if d not in available]
            if missing:
                raise RuntimeError(
                    f"opentargets: dataset(s) {missing} not found in release "
                    f"{version}. Available: {', '.join(available)}"
                )
            selected = list(requested)

        written: list[Path] = []

        # Release-level schema, checksums, and provenance. These are best effort:
        # a missing or transient one should not discard a large data download.
        checksums: dict[str, str] = {}
        root_entries = _list_dir(f"{release_url}/")
        wanted_root = list(_RELEASE_FILES) + [
            n for n in root_entries
            if not n.endswith("/") and is_doc_filename(n) and n not in _RELEASE_FILES
        ]
        for name in wanted_root:
            url = f"{release_url}/{name}"
            try:
                path = _with_retries(
                    lambda u=url, n=name: _download_file(u, dest / n, None)
                )
                written.append(path)
                if name == "release_data_integrity.sha1":
                    checksums = _parse_sha1(path.read_text(errors="replace"))
            except Exception as exc:
                print(f"  opentargets: skipping release file {name} ({exc})")

        if not checksums:
            print(
                "  opentargets: no SHA1 checksums available; "
                "Parquet files will be size-checked only."
            )

        # Per-dataset text descriptions shown on the Downloads page (separate
        # host). Also best effort so an API hiccup does not discard the data.
        try:
            downloads = _with_retries(_graphql_downloads)
            downloads_path = dest / "downloads.json"
            downloads_path.write_text(downloads)
            written.append(downloads_path)
        except Exception as exc:
            print(f"  opentargets: could not fetch dataset descriptions ({exc})")

        # The datasets themselves.
        print(
            f"opentargets {version}: downloading {len(selected)} dataset(s): "
            f"{', '.join(selected)}"
        )
        for ds in selected:
            ds_url = f"{output_url}/{ds}"
            # Skip Spark marker files (e.g. the zero-byte _SUCCESS), which would
            # otherwise fail the empty-file integrity check.
            part_files = [
                n for n in _list_dir(f"{ds_url}/")
                if not n.endswith("/") and not n.startswith("_")
            ]
            if not part_files:
                raise RuntimeError(f"opentargets: no data files found in {ds_url}")
            print(f"  {ds}: {len(part_files)} file(s)")
            for fname in part_files:
                file_url = f"{ds_url}/{fname}"
                local = dest / ds / fname
                expected = checksums.get(fname)
                written.append(
                    _with_retries(
                        lambda u=file_url, l=local, e=expected: _download_file(u, l, e)
                    )
                )

        return written
