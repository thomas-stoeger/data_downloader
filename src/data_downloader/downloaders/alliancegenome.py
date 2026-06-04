"""
Alliance of Genome Resources (AGR) bulk TSV downloader.

The Alliance publishes its bulk download files through a File Management System
(FMS) API. Each quarterly portal release is a "snapshot" of versioned data
files, one set per release (e.g. release `9.0.0`):

    https://fms.alliancegenome.org/api/releaseversion/current
        -> the current release version, read cheaply for `latest_version`.
    https://fms.alliancegenome.org/api/snapshot/release/<version>
        -> the snapshot, a JSON object whose `snapShot.dataFiles` array lists
           every file in the release.

Each dataFile entry carries:

    s3Path      "9.0.0/INTERACTION-MOL/COMBINED/INTERACTION-MOL_COMBINED_12.tsv.gz"
    s3Url       the byte-download URL on https://download.alliancegenome.org/...
    md5Sum      the MD5 of the file's *decompressed* content (Alliance hashes
                the TSV before gzipping it), verified after download
    dataType    {name, fileExtension, ...} e.g. name "ORTHOLOGY-ALLIANCE",
                fileExtension "tsv"
    dataSubType {name, ...} the species / member database the file is for, e.g.
                "FB", "MGI", "RGD", "SGD", "WB", "ZFIN", "HUMAN", "XBXL",
                "XBXT", or "COMBINED" for the cross-MOD combined file (some
                data types subtype by NCBI taxon id instead, e.g.
                "NCBITaxon10090").

This downloader fetches the TSV bulk files (the data types whose
`fileExtension` is `tsv`: orthology, disease, expression, molecular and genetic
interactions, gene descriptions, variant-allele, and the cross-reference
tables) across the selected species. It does not fetch the JSON, GFF, VCF,
OBO, or GAF outputs the same release also offers.

Versioning: the version string is the release version (e.g. `9.0.0`), read from
the FMS `releaseversion/current` endpoint without downloading any data.

Config keys:
    data_types (optional) list of TSV data type names to download, e.g.
               ["ORTHOLOGY-ALLIANCE", "DISEASE-ALLIANCE"]. Use ["*"] (the
               default) for every TSV data type in the release. Names that are
               not present cause a failure that prints the full list available.
    species    (optional) list of dataSubType names to download, e.g.
               ["FB", "MGI", "HUMAN"]. Use ["*"] (the default) for every
               species / subtype. Names that are not present cause a failure
               that prints the full list available.
"""
import hashlib
import json
import ssl
import urllib.request
import zlib
from pathlib import Path

import certifi
from tqdm import tqdm

from .base import BaseDownloader, _with_retries

_API = "https://fms.alliancegenome.org/api"
_CHUNK = 1024 * 1024  # 1 MB
_SSL = ssl.create_default_context(cafile=certifi.where())
# Identify the tool; the default Python-urllib user agent is best avoided.
_HEADERS = {"User-Agent": "data_downloader (+https://www.alliancegenome.org)"}

_TSV_EXTENSION = "tsv"


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers=_HEADERS)


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(_request(url), context=_SSL) as r:
        return json.loads(r.read())


def _latest_version() -> str:
    """Return the current Alliance release version (e.g. "9.0.0")."""
    payload = _get_json(f"{_API}/releaseversion/current")
    version = (payload.get("releaseVersion") or "").strip()
    if not version:
        raise RuntimeError(
            f"Alliance releaseversion/current returned no releaseVersion: {payload}"
        )
    return version


def _tsv_datafiles(version: str) -> list[dict]:
    """Return the dataFile entries in a release snapshot whose data type is TSV."""
    payload = _get_json(f"{_API}/snapshot/release/{version}")
    snapshot = payload.get("snapShot") or {}
    datafiles = snapshot.get("dataFiles")
    if not datafiles:
        raise RuntimeError(
            f"Alliance snapshot for release {version} contains no dataFiles"
        )
    return [
        df
        for df in datafiles
        if (df.get("dataType") or {}).get("fileExtension") == _TSV_EXTENSION
    ]


def _local_path(dest: Path, s3_path: str, version: str) -> Path:
    """Mirror the upstream s3Path under dest, dropping the leading release dir.

    s3Path is "<version>/<DATA-TYPE>/<SUBTYPE>/<filename>"; the release
    component is redundant with the version directory `dest` already names, so
    it is stripped, giving "<DATA-TYPE>/<SUBTYPE>/<filename>". The remaining
    path is upstream data, so it is sanitized: any parent ("..") or absolute
    component is rejected rather than allowed to escape `dest`.
    """
    parts = [p for p in s3_path.split("/") if p not in ("", ".")]
    if parts and parts[0] == version:
        parts = parts[1:]
    if not parts or any(p == ".." for p in parts):
        raise RuntimeError(f"Alliance: refusing unsafe s3Path '{s3_path}'")
    return dest.joinpath(*parts)


def _download_file(url: str, local_path: Path, expected_md5: str | None) -> Path:
    """Stream `url` to `local_path`, then verify size and MD5.

    The raw bytes are written to disk unchanged. The Alliance `md5Sum` is the
    digest of the *decompressed* content, so for a gzipped file the hash is
    computed over the decompressed stream (via a zlib gzip-decompressor fed each
    chunk) rather than the stored bytes; for a plain file it is the raw bytes.
    """
    local_path.parent.mkdir(parents=True, exist_ok=True)
    is_gz = local_path.name.lower().endswith(".gz")
    md5 = hashlib.md5()
    # wbits=47: auto-detect the gzip/zlib header while inflating.
    inflater = zlib.decompressobj(47) if is_gz else None
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
                md5.update(inflater.decompress(chunk) if inflater else chunk)
    if inflater is not None:
        md5.update(inflater.flush())
    if expected_size is not None and bytes_written != expected_size:
        local_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"{local_path.name}: size mismatch, expected {expected_size} bytes, "
            f"got {bytes_written}"
        )
    if expected_md5 and md5.hexdigest() != expected_md5.lower():
        local_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"{local_path.name}: MD5 mismatch, expected {expected_md5}, "
            f"got {md5.hexdigest()}"
        )
    return local_path


def _select(requested, available: set[str], label: str) -> set[str]:
    """Resolve a ["*"]-or-list config value against what the release offers."""
    if not requested or requested == ["*"]:
        return available
    missing = [r for r in requested if r not in available]
    if missing:
        raise RuntimeError(
            f"alliancegenome: {label} {missing} not found in this release. "
            f"{len(available)} available: {', '.join(sorted(available))}"
        )
    return set(requested)


class AllianceGenomeDownloader(BaseDownloader):

    def latest_version(self, config: dict) -> str:
        return _latest_version()

    def fetch(self, config: dict, dest: Path) -> list[Path]:
        # The store names the version directory after latest_version(), so the
        # release version is the directory name; reuse it instead of asking the
        # API again, keeping fetch consistent with the version that was checked.
        version = dest.name

        datafiles = _tsv_datafiles(version)
        if not datafiles:
            raise RuntimeError(
                f"alliancegenome: release {version} has no TSV data files"
            )

        all_types = {(df.get("dataType") or {}).get("name") for df in datafiles}
        all_species = {(df.get("dataSubType") or {}).get("name") for df in datafiles}
        all_types.discard(None)
        all_species.discard(None)

        wanted_types = _select(config.get("data_types"), all_types, "data type(s)")
        wanted_species = _select(config.get("species"), all_species, "species")

        selected = [
            df
            for df in datafiles
            if (df.get("dataType") or {}).get("name") in wanted_types
            and (df.get("dataSubType") or {}).get("name") in wanted_species
        ]
        if not selected:
            raise RuntimeError(
                "alliancegenome: no files match the selected data types and species"
            )

        print(
            f"alliancegenome {version}: downloading {len(selected)} TSV file(s) "
            f"across {len(wanted_types)} data type(s) and "
            f"{len(wanted_species)} species"
        )

        written: list[Path] = []
        for df in selected:
            url = df.get("s3Url")
            s3_path = df.get("s3Path")
            if not url or not s3_path:
                raise RuntimeError(f"alliancegenome: dataFile missing s3Url/s3Path: {df}")
            local = _local_path(dest, s3_path, version)
            expected_md5 = df.get("md5Sum")
            written.append(
                _with_retries(
                    lambda u=url, l=local, m=expected_md5: _download_file(u, l, m)
                )
            )

        return written
