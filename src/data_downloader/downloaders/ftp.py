import ftplib
from datetime import datetime, timezone
from pathlib import Path

from tqdm import tqdm

from .base import BaseDownloader


class FtpDownloader(BaseDownloader):
    """
    Downloads a single file from an FTP server.

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
        local_path = dest / filename

        with ftplib.FTP(host) as ftp:
            ftp.login()
            total = ftp.size(remote_path)
            bytes_written = 0
            with (
                open(local_path, "wb") as f,
                tqdm(
                    total=total,
                    unit="B",
                    unit_scale=True,
                    desc=filename,
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
                f"{filename}: size mismatch — expected {total} bytes, got {bytes_written}"
            )

        return [local_path]

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
