"""
Data root configuration. The download root is read from the DL_DATA_ROOT
environment variable, or from ~/.config/data_downloader/config.toml.
"""
import os
import tomllib
from pathlib import Path

_CONFIG_FILE = Path.home() / ".config" / "data_downloader" / "config.toml"


def get_data_root() -> Path:
    """Return the configured data root directory, creating it if needed."""
    if root := os.environ.get("DL_DATA_ROOT"):
        path = Path(root)
    elif _CONFIG_FILE.exists():
        with open(_CONFIG_FILE, "rb") as f:
            cfg = tomllib.load(f)
        path = Path(cfg["data_root"])
    else:
        raise RuntimeError(
            "No data root configured. Set DL_DATA_ROOT env var, or create "
            f"{_CONFIG_FILE} with:\n\ndata_root = \"/path/to/your/data\""
        )
    path.mkdir(parents=True, exist_ok=True)
    return path


def set_data_root(root: Path) -> None:
    """Persist a data root to the config file."""
    _CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_CONFIG_FILE, "w") as f:
        f.write(f'data_root = "{root}"\n')
