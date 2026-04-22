"""
Creates versioned directories and coordinates the download + manifest update.
"""
import shutil
from pathlib import Path

from ..config import get_data_root
from ..downloaders.registry import get_downloader
from ..registry.loader import get_dataset
from . import manifest as manifest_mod
from .integrity import verify_file


def fetch_dataset(name: str, force: bool = False) -> Path:
    """
    Download the latest version of a dataset.

    Returns the version directory that was written.
    Skips download if the local version is already up-to-date, unless force=True.
    """
    config = get_dataset(name)
    downloader = get_downloader(config["downloader"])

    data_root = get_data_root()
    dataset_root = data_root / name
    dataset_root.mkdir(parents=True, exist_ok=True)

    remote_version = downloader.latest_version(config)
    local_version = manifest_mod.latest_local_version(dataset_root)

    if not force and local_version == remote_version:
        print(f"{name}: already at {remote_version}, skipping.")
        return dataset_root / remote_version

    version_dir = dataset_root / remote_version
    version_dir.mkdir(parents=True, exist_ok=True)

    print(f"{name}: downloading version {remote_version} ...")
    try:
        files = downloader.fetch(config, version_dir)
        print(f"{name}: verifying {len(files)} file(s)...")
        for f in files:
            verify_file(f)
    except Exception:
        shutil.rmtree(version_dir, ignore_errors=True)
        raise

    manifest_mod.record_version(dataset_root, remote_version, files)
    print(f"{name}: done. {len(files)} file(s) written to {version_dir}")
    return version_dir


def check_dataset(name: str) -> dict:
    """
    Check if a newer version is available without downloading.

    Returns a dict with keys: name, local_version, remote_version, up_to_date.
    """
    config = get_dataset(name)
    downloader = get_downloader(config["downloader"])

    data_root = get_data_root()
    dataset_root = data_root / name

    remote_version = downloader.latest_version(config)
    local_version = manifest_mod.latest_local_version(dataset_root)

    return {
        "name": name,
        "local_version": local_version or "none",
        "remote_version": remote_version,
        "up_to_date": local_version == remote_version,
    }
