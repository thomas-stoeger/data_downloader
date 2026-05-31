"""
Ensembl TSV cross-reference downloader.

Ensembl publishes, per species, a set of tab-separated cross-reference tables
under the FTP server (served over HTTPS):

    https://ftp.ensembl.org/pub/current_tsv/<species>/

Each species directory holds the gzipped TSV tables (e.g.
`Homo_sapiens.GRCh38.115.entrez.tsv.gz`, `...refseq.tsv.gz`,
`...uniprot.tsv.gz`, `...ena.tsv.gz`, `...karyotype.tsv.gz`), the matching
`README_*.tsv` descriptions, and a `CHECKSUMS` file. This downloader fetches
every file in each selected species directory into a per-species subdirectory.

`current_tsv` is a symlink to the current release's `tsv` tree. The release
number is read cheaply from

    https://ftp.ensembl.org/pub/VERSION

which returns a single integer (e.g. `115`); that integer is the version
string, so "is this current?" is answered without downloading anything.

Checksums: each `CHECKSUMS` file lists, per file, the output of the classic
Unix `sum` command (the BSD algorithm): a 16-bit checksum and a 1024-byte
block count. Downloaded files are verified against it where present. Not every
folder ships a CHECKSUMS file, so verification is best effort; files always get
the size check and gzip files the archive check in addition.

Config keys:
    species  (required) list of species directory names to download, e.g.
             ["homo_sapiens", "mus_musculus"]. Use ["*"] to fetch every species
             in the release (large: 350+ species). Species names that are not
             present cause a failure that prints the full list available.
"""
import math
import re
import ssl
import urllib.request
from pathlib import Path

import certifi
from tqdm import tqdm

from .base import BaseDownloader, _with_retries

_PUB = "https://ftp.ensembl.org/pub"
_SUBDIR = "current_tsv"
_CHUNK = 1024 * 1024  # 1 MB
_SSL = ssl.create_default_context(cafile=certifi.where())
# Identify the tool; the default Python-urllib user agent is best avoided.
_HEADERS = {"User-Agent": "data_downloader (+https://www.ensembl.org)"}

_HREF_RE = re.compile(r'href="([^"]+)"')


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers=_HEADERS)


def _list_dir(url: str) -> list[str]:
    """Return the entry names in an Apache autoindex directory.

    Directory names keep their trailing slash; column-sort links and the
    parent/absolute links the autoindex emits are dropped.
    """
    with urllib.request.urlopen(_request(url), context=_SSL) as r:
        html = r.read().decode("utf-8", errors="replace")
    names: list[str] = []
    for href in _HREF_RE.findall(html):
        if href.startswith("?") or href.startswith("/") or href == "../":
            continue
        names.append(href)
    return names


def _latest_version() -> str:
    """Return the Ensembl release number (e.g. "115") from /pub/VERSION."""
    with urllib.request.urlopen(_request(f"{_PUB}/VERSION"), context=_SSL) as r:
        version = r.read().decode("utf-8", errors="replace").strip()
    if not version:
        raise RuntimeError(f"Empty VERSION file at {_PUB}/VERSION")
    return version


def _bsd_sum(path: Path) -> tuple[int, int]:
    """Compute the classic Unix `sum` (BSD algorithm) of a file.

    Returns (checksum, blocks): a 16-bit rotate-and-add checksum and the
    1024-byte block count, matching the two leading columns of an Ensembl
    CHECKSUMS line.
    """
    checksum = 0
    size = 0
    with open(path, "rb") as f:
        while chunk := f.read(_CHUNK):
            size += len(chunk)
            for byte in chunk:
                checksum = (checksum >> 1) + ((checksum & 1) << 15)
                checksum = (checksum + byte) & 0xFFFF
    return checksum, math.ceil(size / 1024)


def _parse_checksums(text: str) -> dict[str, tuple[int, int]]:
    """Parse a `<checksum> <blocks> <path...> <filename>` CHECKSUMS file into
    {basename: (checksum, blocks)}.

    Keys are file basenames so a checksum matches regardless of the path prefix
    the file records (Ensembl writes an absolute build path before the name).
    """
    out: dict[str, tuple[int, int]] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 3 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        basename = Path(parts[-1]).name
        out[basename] = (int(parts[0]), int(parts[1]))
    return out


def _download_file(
    url: str, local_path: Path, expected_sum: tuple[int, int] | None
) -> Path:
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
    if expected_sum is not None:
        actual = _bsd_sum(local_path)
        if actual != expected_sum:
            local_path.unlink(missing_ok=True)
            raise RuntimeError(
                f"{local_path.name}: checksum mismatch, expected sum "
                f"{expected_sum[0]}/{expected_sum[1]} blocks, got "
                f"{actual[0]}/{actual[1]} blocks"
            )
    return local_path


class EnsemblTsvDownloader(BaseDownloader):

    def latest_version(self, config: dict) -> str:
        return _latest_version()

    def fetch(self, config: dict, dest: Path) -> list[Path]:
        requested = config.get("species")
        base_url = f"{_PUB}/{_SUBDIR}"

        available = sorted(
            n.rstrip("/") for n in _list_dir(f"{base_url}/") if n.endswith("/")
        )

        if not requested:
            raise RuntimeError(
                "ensembl_tsv: set 'species' in the registry to choose which "
                'species to download (use ["*"] for every species). '
                f"{len(available)} available, e.g.: "
                f"{', '.join(available[:8])}, ..."
            )

        if requested == ["*"]:
            selected = available
        else:
            missing = [s for s in requested if s not in available]
            if missing:
                raise RuntimeError(
                    f"ensembl_tsv: species {missing} not found in current "
                    f"release. {len(available)} available, e.g.: "
                    f"{', '.join(available[:8])}, ..."
                )
            selected = list(requested)

        print(
            f"ensembl_tsv: downloading {len(selected)} species: "
            f"{', '.join(selected)}"
        )

        written: list[Path] = []
        for species in selected:
            species_url = f"{base_url}/{species}"
            entries = [n for n in _list_dir(f"{species_url}/") if not n.endswith("/")]
            if not entries:
                raise RuntimeError(f"ensembl_tsv: no files found in {species_url}")

            # Parse CHECKSUMS first so every other file in the folder can be
            # verified against it. A folder without one is verified by size
            # (and the gzip archive check) only.
            checksums: dict[str, tuple[int, int]] = {}
            if "CHECKSUMS" in entries:
                url = f"{species_url}/CHECKSUMS"
                path = _with_retries(
                    lambda u=url: _download_file(u, dest / species / "CHECKSUMS", None)
                )
                written.append(path)
                checksums = _parse_checksums(path.read_text(errors="replace"))

            data_files = [n for n in entries if n != "CHECKSUMS"]
            print(f"  {species}: {len(data_files)} file(s)")
            for name in data_files:
                file_url = f"{species_url}/{name}"
                local = dest / species / name
                expected = checksums.get(name)
                written.append(
                    _with_retries(
                        lambda u=file_url, l=local, e=expected: _download_file(u, l, e)
                    )
                )

        return written
