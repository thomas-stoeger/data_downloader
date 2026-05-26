"""
PubTator3 downloader.

Pulls files from a single directory on ftp.ncbi.nlm.nih.gov (default
/pub/lu/PubTator3). The version string is the most recent MDTM across the
selected files, formatted YYYY-MM-DD. NCBI refreshes the snapshot in lockstep
each month, so a single max-MDTM fairly represents the snapshot date.

PubTator3 does not publish checksum sidecars. Each file is verified by size
match during transfer and gzip read-through afterwards via the standard
store pipeline; a failure in any file wipes the version directory.

README and other companion docs (CHANGES, CHANGELOG, NOTES, RELEASE_NOTES)
present in the directory are downloaded alongside the data files. They do
not contribute to the version string, so an isolated README touch upstream
does not look like a new snapshot.

Required config keys:
    host  - FTP hostname
    path  - directory containing the PubTator3 files (no trailing slash)
Optional config keys:
    files - explicit list of filenames to download. If omitted, every
            *.gz file in the directory is taken (~225 GB including the
            BioCXML.{0-9}.tar.gz archives). Companion docs are picked up
            regardless of this allowlist.
"""
import ftplib
from datetime import datetime, timezone
from pathlib import Path

from tqdm import tqdm

from .base import BaseDownloader, _with_retries, is_doc_filename

_FTP_TIMEOUT = 60


def _connect(host: str) -> ftplib.FTP:
    ftp = ftplib.FTP(host, timeout=_FTP_TIMEOUT)
    ftp.login()
    return ftp


def _list_gz_files(ftp: ftplib.FTP, remote_dir: str) -> list[str]:
    entries = ftp.nlst(remote_dir)
    return sorted(
        Path(e).name for e in entries if Path(e).name.lower().endswith(".gz")
    )


def _list_doc_files(ftp: ftplib.FTP, remote_dir: str) -> list[str]:
    entries = ftp.nlst(remote_dir)
    return sorted(
        Path(e).name for e in entries if is_doc_filename(Path(e).name)
    )


def _mdtm(ftp: ftplib.FTP, remote_path: str) -> datetime:
    resp = ftp.sendcmd(f"MDTM {remote_path}")
    timestamp_str = resp.split()[1]
    return datetime.strptime(timestamp_str, "%Y%m%d%H%M%S").replace(
        tzinfo=timezone.utc
    )


def _retrieve(ftp: ftplib.FTP, remote_path: str, local_path: Path) -> None:
    # ftp.nlst() / LIST flip the server to ASCII (TYPE A); NCBI then refuses
    # SIZE in that mode, so set TYPE I before asking.
    ftp.voidcmd("TYPE I")
    total = ftp.size(remote_path)
    bytes_written = 0
    with (
        open(local_path, "wb") as f,
        tqdm(total=total, unit="B", unit_scale=True, desc=local_path.name) as bar,
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
            f"{local_path.name}: size mismatch - expected {total}, got {bytes_written}"
        )


def _selected_filenames(config: dict, ftp: ftplib.FTP, remote_dir: str) -> list[str]:
    explicit = config.get("files")
    if explicit:
        return list(explicit)
    return _list_gz_files(ftp, remote_dir)


class Pubtator3Downloader(BaseDownloader):

    def latest_version(self, config: dict) -> str:
        host = config["host"]
        remote_dir = config["path"].rstrip("/")
        with _connect(host) as ftp:
            names = _selected_filenames(config, ftp, remote_dir)
            if not names:
                raise RuntimeError(f"No PubTator3 files found at {remote_dir}")
            mtimes = [_mdtm(ftp, f"{remote_dir}/{n}") for n in names]
        return max(mtimes).strftime("%Y-%m-%d")

    def fetch(self, config: dict, dest: Path) -> list[Path]:
        host = config["host"]
        remote_dir = config["path"].rstrip("/")
        with _connect(host) as ftp:
            data_names = _selected_filenames(config, ftp, remote_dir)
            doc_names = _list_doc_files(ftp, remote_dir)

        written: list[Path] = []
        for name in data_names + doc_names:
            remote_path = f"{remote_dir}/{name}"
            local_path = dest / name

            def _do() -> None:
                with _connect(host) as ftp:
                    _retrieve(ftp, remote_path, local_path)

            _with_retries(_do)
            written.append(local_path)

        return written
