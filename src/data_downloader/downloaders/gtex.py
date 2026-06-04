"""
GTEx downloader.

The GTEx (Genotype-Tissue Expression) project distributes its open-access bulk
files from a public Google Cloud Storage bucket (`adult-gtex`):

    https://storage.googleapis.com/adult-gtex/bulk-gex/<version>/rna-seq/<file>

This downloader fetches a configured set of those objects. The default set is
the v10 gene-level RNA-seq expression matrices (gene median TPM, gene reads,
gene TPM), which give per-gene tissue-expression breadth.

Versioning: GTEx releases are pinned by config, not auto-detected. The bucket
keeps several version prefixes (v8, v10, v11, ...) and newer ones can be
placeholders that do not yet contain the matrices, so the version string is the
configured `version` (default `v10`), and the file names embed it. This mirrors
the optional release pin used by the Open Targets downloader.

Each object is verified against the MD5 that GCS returns in the download
response (`x-goog-hash: md5=<base64>`), plus the size check and the gzip
read-through (`verify_file`) for the `.gct.gz` files.

Config keys:
    bucket   (optional) GCS bucket name. Defaults to `adult-gtex`.
    version  (optional) release string used as the local version directory.
             Defaults to `v10`.
    files    (optional) list of object names (paths within the bucket) to
             download. Defaults to the v10 gene-level expression matrices.
"""
import base64
import hashlib
import re
import ssl
import urllib.request
from pathlib import Path

import certifi
from tqdm import tqdm

from .base import BaseDownloader, _with_retries

_CHUNK = 1024 * 1024  # 1 MB
_SSL = ssl.create_default_context(cafile=certifi.where())
_HEADERS = {"User-Agent": "data_downloader (+https://gtexportal.org)"}
_MD5_RE = re.compile(r"md5=([A-Za-z0-9+/=]+)")

_DEFAULT_BUCKET = "adult-gtex"
_DEFAULT_VERSION = "v10"
_DEFAULT_FILES = [
    "bulk-gex/v10/rna-seq/GTEx_Analysis_v10_RNASeQCv2.4.2_gene_median_tpm.gct.gz",
    "bulk-gex/v10/rna-seq/GTEx_Analysis_v10_RNASeQCv2.4.2_gene_reads.gct.gz",
    "bulk-gex/v10/rna-seq/GTEx_Analysis_v10_RNASeQCv2.4.2_gene_tpm.gct.gz",
]


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers=_HEADERS)


def _md5_from_goog_hash(headers) -> str | None:
    """Return the hex MD5 from the GCS `x-goog-hash` header(s) (which carry
    `crc32c=...` and `md5=<base64>`), or None if absent."""
    values = headers.get_all("X-Goog-Hash") or []
    for value in values:
        m = _MD5_RE.search(value)
        if m:
            return base64.b64decode(m.group(1)).hex()
    return None


def _download_file(url: str, local_path: Path) -> Path:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    md5 = hashlib.md5()
    bytes_written = 0
    with urllib.request.urlopen(_request(url), context=_SSL) as response:
        expected_size = int(response.headers.get("Content-Length", 0)) or None
        expected_md5 = _md5_from_goog_hash(response.headers)
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


class GtexDownloader(BaseDownloader):

    def latest_version(self, config: dict) -> str:
        return config.get("version") or _DEFAULT_VERSION

    def fetch(self, config: dict, dest: Path) -> list[Path]:
        bucket = config.get("bucket") or _DEFAULT_BUCKET
        version = config.get("version") or _DEFAULT_VERSION
        files = config.get("files") or _DEFAULT_FILES

        print(f"gtex {version}: downloading {len(files)} file(s) from gs://{bucket}")
        written: list[Path] = []
        for name in files:
            url = f"https://storage.googleapis.com/{bucket}/{name}"
            local_name = name.rsplit("/", 1)[-1]
            written.append(
                _with_retries(lambda u=url, n=local_name: _download_file(u, dest / n))
            )
        return written
