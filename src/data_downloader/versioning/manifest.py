"""
Per-dataset manifest: tracks all downloaded versions.

manifest.json lives at {data_root}/{dataset_name}/manifest.json
Each version entry records when it was downloaded, what version string
it corresponds to, and which files were written.
"""
import json
from datetime import datetime, timezone
from pathlib import Path


def _manifest_path(dataset_root: Path) -> Path:
    return dataset_root / "manifest.json"


def load_manifest(dataset_root: Path) -> dict:
    p = _manifest_path(dataset_root)
    if p.exists():
        return json.loads(p.read_text())
    return {"versions": []}


def save_manifest(dataset_root: Path, manifest: dict) -> None:
    _manifest_path(dataset_root).write_text(
        json.dumps(manifest, indent=2, default=str)
    )


def record_version(
    dataset_root: Path,
    version: str,
    files: list[Path],
    license: dict | None = None,
) -> None:
    """Append a new version entry to the manifest.

    `license`, when present, is the per-version license record produced by
    versioning/license.py (provenance plus the hashes of the captured license
    copies, or an error if capture failed). License copies live under the
    version's `_license/` dir and are recorded here, not in `files`.
    """
    manifest = load_manifest(dataset_root)
    entry = {
        "version": version,
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "files": [str(f.relative_to(dataset_root)) for f in files],
    }
    if license is not None:
        entry["license"] = license
    manifest["versions"].append(entry)
    save_manifest(dataset_root, manifest)


def latest_license_record(dataset_root: Path) -> dict | None:
    """Return the license record of the most recent version that has one, or
    None. Used to compare a freshly captured license against the last one."""
    for entry in reversed(load_manifest(dataset_root)["versions"]):
        if entry.get("license"):
            return entry["license"]
    return None


def latest_local_version(dataset_root: Path) -> str | None:
    """Return the version string of the most recently downloaded version, or None."""
    manifest = load_manifest(dataset_root)
    if manifest["versions"]:
        return manifest["versions"][-1]["version"]
    return None


def all_local_versions(dataset_root: Path) -> list[dict]:
    return load_manifest(dataset_root)["versions"]
