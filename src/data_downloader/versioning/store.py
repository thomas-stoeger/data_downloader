"""
Creates versioned directories and coordinates the download + manifest update.
"""
import shutil
from pathlib import Path

from ..config import get_data_root
from ..downloaders.registry import get_downloader
from ..registry.loader import get_dataset
from . import license as license_mod
from . import manifest as manifest_mod
from .integrity import verify_file


def fetch_dataset(name: str, force: bool = False) -> Path:
    """
    Download the latest version of a dataset.

    Returns the version directory that was written.
    Skips download if the local version is already up-to-date, unless force=True.
    """
    config = get_dataset(name)
    # Expose the dataset name to downloaders that need to consult the local
    # manifest while computing a version (e.g. google_sheet_hashed).
    config["_dataset_name"] = name
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

    # Capture the license alongside the data. This is best-effort: a license
    # download failure must not discard an already-verified data snapshot, so
    # it is recorded as an error rather than raised.
    license_record = _capture_license(name, config, dataset_root, version_dir)

    manifest_mod.record_version(dataset_root, remote_version, files, license=license_record)
    print(f"{name}: done. {len(files)} file(s) written to {version_dir}")
    return version_dir


def _capture_license(
    name: str, config: dict, dataset_root: Path, version_dir: Path
) -> dict | None:
    """Capture and verify the license for this version, compare it to the last
    recorded license, and return the record (or None if no license is
    configured). Never raises: capture failures are recorded, not fatal."""
    license_cfg = config.get("license")
    if not license_cfg:
        return None

    prev_record = manifest_mod.latest_license_record(dataset_root)
    try:
        record = license_mod.capture_license(license_cfg, version_dir)
        for doc in record.get("documents", []):
            verify_file(version_dir.parent / doc["file"])
        new_hashes = {
            doc["url"]: doc["sha256"] for doc in record.get("documents", [])
        }
        if prev_record and license_mod.has_changed(prev_record, new_hashes):
            record["changed_from_previous"] = True
            print(
                f"  *** LICENSE CHANGED for {name}: the license differs from the "
                f"previously captured version. Review before redistributing. ***"
            )
        print(f"{name}: captured license ({len(new_hashes)} document(s)).")
        return record
    except Exception as exc:
        print(f"  warning: license capture failed for {name}: {exc}")
        record = license_mod._common(license_cfg)
        record["error"] = str(exc)
        return record


def check_dataset(name: str) -> dict:
    """
    Check if a newer version is available without downloading.

    Returns a dict with keys: name, local_version, remote_version, up_to_date.
    """
    config = get_dataset(name)
    # See fetch_dataset: lets manifest-aware downloaders date a snapshot.
    config["_dataset_name"] = name
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
        "license_status": _check_license(config, dataset_root),
    }


def _check_license(config: dict, dataset_root: Path) -> str:
    """Compare the current upstream license to the last recorded one without
    writing anything. Returns one of: none, new, ok, changed, error.

    Tiny by design (license docs are kilobytes), so it is cheap enough to run
    during `check` and catches a license change even when the data version is
    unchanged."""
    license_cfg = config.get("license")
    if not license_cfg:
        return "none"
    prev_record = manifest_mod.latest_license_record(dataset_root)
    if not license_cfg.get("url"):
        # Provenance-only license (no fetchable copy): nothing to compare.
        return "ok" if prev_record else "new"
    try:
        new_hashes = license_mod.license_fingerprint(license_cfg)
    except Exception:
        return "error"
    if not prev_record or not prev_record.get("documents"):
        return "new"
    return "changed" if license_mod.has_changed(prev_record, new_hashes) else "ok"
