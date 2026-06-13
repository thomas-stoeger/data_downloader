"""
PMC bulk downloader.

Mirrors PubMed Central bulk article archives served from
ftp.ncbi.nlm.nih.gov: the Open Access Subset (oa_bulk), the Author Manuscript
Dataset (manuscript), and the Historical OCR collection (historical_ocr).

Each dataset is a set of leaf FTP directories holding gzipped tar archives.
The oa_bulk and manuscript datasets split each leaf directory into a dated
"baseline" (a full corpus snapshot, chunked by PMC-ID range) plus daily
"incremental" archives added since; every data tarball is accompanied by
.filelist.csv / .filelist.txt companions listing the PMCIDs, citation, and
per-article license it contains. The historical_ocr directory is flat: one
<journal>.tar.gz per title, with no baseline/incremental split.

Leaf directories are mirrored under the version directory preserving their
relative subdirectory (e.g. oa_comm/txt/...), so the group and format layout is
kept on disk and the self-describing filenames never collide.

Version string: the most recent date embedded in the selected baseline/incr
filenames (YYYY-MM-DD). Directories with no dated filenames (historical_ocr)
fall back to the most recent file modification time (MDTM). Both are read from
the directory listing without downloading any archive. Because the default
config includes the daily incrementals, the version advances each day new
incrementals are published (like nih_exporter); restrict `modes` to "baseline"
for a stable, reproducible snapshot dated by the baseline build.

PMC publishes no checksum sidecars. Each tarball is verified by size match
during transfer and gzip read-through afterwards via the standard store
pipeline; a failure in any file wipes the version directory.

Required config keys:
    host  - FTP hostname
    path  - base directory on the server
Optional config keys:
    subdirs - relative leaf directories under `path` to mirror (e.g.
              ["oa_comm/txt", "oa_comm/xml"]). If omitted, `path` itself is the
              single leaf directory.
    modes   - which dated archive sets to take, among "baseline" and
              "incremental". Defaults to both. Tarballs whose names match
              neither pattern (e.g. the per-journal historical_ocr archives)
              are always taken.
    files   - explicit allowlist of tarball basenames. When set, only these
              .tar.gz files are taken (their filelist companions still follow).
"""
import ftplib
import re
from datetime import datetime, timezone
from pathlib import Path

from tqdm import tqdm

from .base import BaseDownloader, _with_retries, is_doc_filename

_FTP_TIMEOUT = 60
_DATE_IN_NAME = re.compile(r"\.(?:baseline|incr)\.(\d{4}-\d{2}-\d{2})\.")
_ALL_MODES = ("baseline", "incremental")


def _connect(host: str) -> ftplib.FTP:
    ftp = ftplib.FTP(host, timeout=_FTP_TIMEOUT)
    ftp.login()
    return ftp


def _leaf_dirs(config: dict) -> list[tuple[str, str]]:
    """Return (remote_dir, relative_subdir) for each leaf directory to mirror.

    relative_subdir is "" when `path` itself is the single leaf (flat layout)."""
    base = config["path"].rstrip("/")
    subdirs = config.get("subdirs")
    if not subdirs:
        return [(base, "")]
    return [(f"{base}/{s.strip('/')}", s.strip("/")) for s in subdirs]


def _tarball_mode(name: str) -> str | None:
    if ".baseline." in name:
        return "baseline"
    if ".incr." in name:
        return "incremental"
    return None


def _select_tarballs(names: list[str], config: dict) -> list[str]:
    """Basenames of the .tar.gz archives to download from one leaf directory."""
    allow = config.get("files")
    modes = config.get("modes") or list(_ALL_MODES)
    selected = []
    for name in names:
        if not name.endswith(".tar.gz"):
            continue
        if allow is not None and name not in allow:
            continue
        mode = _tarball_mode(name)
        # Un-patterned tarballs (e.g. historical_ocr journals) are always kept;
        # dated baseline/incr tarballs only when their mode is selected.
        if mode is not None and mode not in modes:
            continue
        selected.append(name)
    return sorted(selected)


def _companions(tarball: str, names: set[str]) -> list[str]:
    """The .filelist.csv / .filelist.txt files that accompany `tarball`."""
    prefix = tarball[: -len(".tar.gz")]
    return [
        prefix + ext
        for ext in (".filelist.csv", ".filelist.txt")
        if prefix + ext in names
    ]


def _basenames(ftp: ftplib.FTP, remote_dir: str) -> list[str]:
    return [Path(e).name for e in ftp.nlst(remote_dir)]


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


class PmcDownloader(BaseDownloader):

    def latest_version(self, config: dict) -> str:
        host = config["host"]
        dated: list[str] = []
        selections: list[tuple[str, list[str]]] = []
        with _connect(host) as ftp:
            for remote_dir, _rel in _leaf_dirs(config):
                tarballs = _select_tarballs(_basenames(ftp, remote_dir), config)
                if not tarballs:
                    continue
                selections.append((remote_dir, tarballs))
                for name in tarballs:
                    m = _DATE_IN_NAME.search(name)
                    if m:
                        dated.append(m.group(1))
            if not selections:
                raise RuntimeError(f"No PMC archives found under {config['path']}")
            if dated:
                # Dates are zero-padded YYYY-MM-DD, so lexical max is chronological.
                return max(dated)
            # No dated filenames (historical_ocr): use the newest MDTM instead.
            mtimes = [
                _mdtm(ftp, f"{remote_dir}/{name}")
                for remote_dir, tarballs in selections
                for name in tarballs
            ]
        return max(mtimes).strftime("%Y-%m-%d")

    def fetch(self, config: dict, dest: Path) -> list[Path]:
        host = config["host"]
        plan: list[tuple[str, Path]] = []
        with _connect(host) as ftp:
            for remote_dir, rel in _leaf_dirs(config):
                names = _basenames(ftp, remote_dir)
                nameset = set(names)
                wanted: list[str] = []
                for tarball in _select_tarballs(names, config):
                    wanted.append(tarball)
                    wanted.extend(_companions(tarball, nameset))
                wanted.extend(sorted(n for n in names if is_doc_filename(n)))
                local_dir = dest / rel if rel else dest
                for name in wanted:
                    plan.append((f"{remote_dir}/{name}", local_dir / name))

        if not plan:
            raise RuntimeError(f"No PMC archives found under {config['path']}")

        written: list[Path] = []
        for remote_path, local_path in plan:
            local_path.parent.mkdir(parents=True, exist_ok=True)

            def _do() -> None:
                with _connect(host) as ftp:
                    _retrieve(ftp, remote_path, local_path)

            _with_retries(_do)
            written.append(local_path)

        return written
