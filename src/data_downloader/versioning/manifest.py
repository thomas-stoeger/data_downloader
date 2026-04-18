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
) -> None:
    """Append a new version entry to the manifest."""
    manifest = load_manifest(dataset_root)
    manifest["versions"].append(
        {
            "version": version,
            "downloaded_at": datetime.now(timezone.utc).isoformat(),
            "files": [str(f.relative_to(dataset_root)) for f in files],
        }
    )
    save_manifest(dataset_root, manifest)


def latest_local_version(dataset_root: Path) -> str | None:
    """Return the version string of the most recently downloaded version, or None."""
    manifest = load_manifest(dataset_root)
    if manifest["versions"]:
        return manifest["versions"][-1]["version"]
    return None


def all_local_versions(dataset_root: Path) -> list[dict]:
    return load_manifest(dataset_root)["versions"]
