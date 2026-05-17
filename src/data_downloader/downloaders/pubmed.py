"""
PubMed downloader — baseline + updatefiles, with MD5 verification.

Downloads every `pubmed{YY}n####.xml.gz` file from
    ftp.ncbi.nlm.nih.gov:/pubmed/baseline/
    ftp.ncbi.nlm.nih.gov:/pubmed/updatefiles/
Each file is checked against its companion `.md5` sidecar published by
NCBI; mismatches trigger up to 3 fresh downloads before giving up.

The version string is the four-digit baseline year (e.g. "2026"),
derived from the two-digit prefix used in baseline filenames. Within
a baseline year, files already present locally are skipped, so
re-running `dl fetch ncbi_pubmed --force` pulls only newly-published
update files.

No config keys required.
"""
import ftplib
import hashlib
import re
from pathlib import Path

from tqdm import tqdm

from .base import BaseDownloader

_HOST = "ftp.ncbi.nlm.nih.gov"
_BASELINE_DIR = "/pubmed/baseline"
_UPDATES_DIR = "/pubmed/updatefiles"
_XML_PATTERN = re.compile(r"^pubmed(\d{2})n\d+\.xml\.gz$")
_MD5_PATTERN = re.compile(r"=\s*([0-9a-fA-F]{32})")
_DOWNLOAD_ATTEMPTS = 3
_CHUNK = 1024 * 1024  # 1 MB
_FTP_TIMEOUT = 60


def _connect() -> ftplib.FTP:
    ftp = ftplib.FTP(_HOST, timeout=_FTP_TIMEOUT)
    ftp.login()
    return ftp


def _list_xml_files(ftp: ftplib.FTP, remote_dir: str) -> list[str]:
    entries = ftp.nlst(remote_dir)
    names = sorted({Path(e).name for e in entries if _XML_PATTERN.match(Path(e).name)})
    return names


def _baseline_year_prefix(filenames: list[str]) -> str:
    prefixes = {m.group(1) for f in filenames if (m := _XML_PATTERN.match(f))}
    if len(prefixes) != 1:
        raise RuntimeError(
            f"Expected a single PubMed baseline year prefix, got: {sorted(prefixes)}"
        )
    return next(iter(prefixes))


def _parse_md5(text: str) -> str:
    m = _MD5_PATTERN.search(text)
    if not m:
        raise RuntimeError(f"Could not parse MD5 from: {text!r}")
    return m.group(1).lower()


def _file_md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        while chunk := f.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _retrieve(ftp: ftplib.FTP, remote_path: str, local_path: Path) -> None:
    total = ftp.size(remote_path)
    bytes_written = 0
    with (
        open(local_path, "wb") as f,
        tqdm(
            total=total,
            unit="B",
            unit_scale=True,
            desc=local_path.name,
            leave=False,
        ) as bar,
    ):
        def write_chunk(chunk: bytes) -> None:
            nonlocal bytes_written
            f.write(chunk)
            bytes_written += len(chunk)
            bar.update(len(chunk))

        ftp.retrbinary(f"RETR {remote_path}", write_chunk)

    if total is not None and bytes_written != total:
        local_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"{local_path.name}: size mismatch — expected {total}, got {bytes_written}"
        )


def _fetch_md5(remote_path: str) -> tuple[str, bytes]:
    """Return (expected_md5, raw_md5_file_bytes) from the FTP server."""
    buf = bytearray()
    with _connect() as ftp:
        ftp.retrbinary(f"RETR {remote_path}", buf.extend)
    raw = bytes(buf)
    return _parse_md5(raw.decode("utf-8", errors="replace")), raw


def _download_with_md5(remote_dir: str, filename: str, dest: Path) -> tuple[Path, Path]:
    xml_remote = f"{remote_dir}/{filename}"
    md5_remote = f"{xml_remote}.md5"
    xml_local = dest / filename
    md5_local = dest / f"{filename}.md5"

    expected, md5_bytes = _fetch_md5(md5_remote)
    md5_local.write_bytes(md5_bytes)

    last_error: str | None = None
    for attempt in range(_DOWNLOAD_ATTEMPTS):
        try:
            with _connect() as ftp:
                _retrieve(ftp, xml_remote, xml_local)
            actual = _file_md5(xml_local)
            if actual == expected:
                return xml_local, md5_local
            last_error = f"MD5 mismatch (expected {expected}, got {actual})"
        except Exception as exc:
            last_error = str(exc)
        if xml_local.exists():
            xml_local.unlink(missing_ok=True)
        if attempt < _DOWNLOAD_ATTEMPTS - 1:
            print(f"  {filename}: attempt {attempt + 1} failed ({last_error}), retrying...")

    raise RuntimeError(
        f"{filename}: failed MD5 verification after {_DOWNLOAD_ATTEMPTS} attempts ({last_error})"
    )


class PubmedDownloader(BaseDownloader):

    def latest_version(self, config: dict) -> str:
        with _connect() as ftp:
            baseline = _list_xml_files(ftp, _BASELINE_DIR)
        if not baseline:
            raise RuntimeError("No PubMed baseline files found on the FTP server.")
        return f"20{_baseline_year_prefix(baseline)}"

    def fetch(self, config: dict, dest: Path) -> list[Path]:
        written: list[Path] = []
        for remote_dir, label in [(_BASELINE_DIR, "baseline"), (_UPDATES_DIR, "updatefiles")]:
            with _connect() as ftp:
                files = _list_xml_files(ftp, remote_dir)
            print(f"  {label}: {len(files)} file(s) on remote")

            subdir = dest / label
            subdir.mkdir(parents=True, exist_ok=True)

            for filename in tqdm(files, desc=label, unit="file"):
                xml_local = subdir / filename
                md5_local = subdir / f"{filename}.md5"
                if xml_local.exists() and md5_local.exists():
                    # Previously verified — trust the local file.
                    written.append(xml_local)
                    written.append(md5_local)
                    continue
                xml_path, md5_path = _download_with_md5(remote_dir, filename, subdir)
                written.append(xml_path)
                written.append(md5_path)

        return written
