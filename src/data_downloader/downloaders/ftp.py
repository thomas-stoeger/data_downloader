import ftplib
from datetime import datetime, timezone
from pathlib import Path

from tqdm import tqdm

from .base import BaseDownloader, is_doc_filename


def _retrieve(ftp: ftplib.FTP, remote_path: str, local_path: Path) -> None:
    filename = local_path.name
    # ftp.nlst() and other LIST-style commands flip the server to ASCII (TYPE A);
    # NCBI then refuses SIZE in that mode, so set TYPE I before asking.
    ftp.voidcmd("TYPE I")
    total = ftp.size(remote_path)
    bytes_written = 0
    with (
        open(local_path, "wb") as f,
        tqdm(total=total, unit="B", unit_scale=True, desc=filename) as bar,
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
            f"{filename}: size mismatch — expected {total} bytes, got {bytes_written}"
        )


def _list_doc_files(ftp: ftplib.FTP, remote_dir: str) -> list[str]:
    """Return README/CHANGES/NOTES/CHANGELOG-shaped filenames in `remote_dir`."""
    try:
        entries = ftp.nlst(remote_dir)
    except ftplib.error_perm:
        return []
    return sorted(
        {Path(e).name for e in entries if is_doc_filename(Path(e).name)}
    )


class FtpDownloader(BaseDownloader):
    """
    Downloads a single file from an FTP server, plus any README-shaped
    companion docs (README, CHANGES, CHANGELOG, NOTES, RELEASE_NOTES) found
    in the same remote directory.

    Required config keys:
        host  — FTP hostname
        path  — full path to the file on the server
    """

    def latest_version(self, config: dict) -> str:
        """Return the file's modification time as YYYY-MM-DD."""
        mtime = self._remote_mtime(config)
        return mtime.strftime("%Y-%m-%d")

    def fetch(self, config: dict, dest: Path) -> list[Path]:
        host = config["host"]
        remote_path = config["path"]
        filename = Path(remote_path).name
        remote_dir = str(Path(remote_path).parent).rstrip("/")

        written: list[Path] = []
        with ftplib.FTP(host) as ftp:
            ftp.login()
            data_local = dest / filename
            _retrieve(ftp, remote_path, data_local)
            written.append(data_local)

            for doc_name in _list_doc_files(ftp, remote_dir):
                doc_local = dest / doc_name
                _retrieve(ftp, f"{remote_dir}/{doc_name}", doc_local)
                written.append(doc_local)

        return written

    def _remote_mtime(self, config: dict) -> datetime:
        host = config["host"]
        remote_path = config["path"]
        with ftplib.FTP(host) as ftp:
            ftp.login()
            resp = ftp.sendcmd(f"MDTM {remote_path}")
            # Response: "213 YYYYMMDDHHmmss"
            timestamp_str = resp.split()[1]
            return datetime.strptime(timestamp_str, "%Y%m%d%H%M%S").replace(
                tzinfo=timezone.utc
            )
