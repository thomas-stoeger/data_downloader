"""
Post-download integrity verification.

Raises RuntimeError for any file that fails its check.
"""
import gzip
import zipfile
from pathlib import Path


def verify_file(path: Path) -> None:
    """Verify a downloaded file is intact based on its extension."""
    if not path.exists():
        raise RuntimeError(f"File missing after download: {path}")
    if path.stat().st_size == 0:
        raise RuntimeError(f"File is empty after download: {path}")

    suffix = "".join(path.suffixes).lower()

    if suffix.endswith(".gz"):
        _verify_gzip(path)
    elif suffix.endswith(".zip"):
        _verify_zip(path)


def _verify_gzip(path: Path) -> None:
    try:
        with gzip.open(path, "rb") as fh:
            while fh.read(1024 * 1024):
                pass
    except Exception as e:
        raise RuntimeError(f"Gzip integrity check failed for {path.name}: {e}") from e


def _verify_zip(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as zf:
            bad = zf.testzip()
            if bad is not None:
                raise RuntimeError(
                    f"Zip integrity check failed for {path.name}: first bad file is '{bad}'"
                )
    except zipfile.BadZipFile as e:
        raise RuntimeError(f"Zip integrity check failed for {path.name}: {e}") from e
