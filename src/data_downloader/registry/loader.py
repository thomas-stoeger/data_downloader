import tomllib
from pathlib import Path

_DATASETS_FILE = Path(__file__).parent / "datasets.toml"


def load_registry() -> dict[str, dict]:
    """Return all dataset configs keyed by dataset name."""
    with open(_DATASETS_FILE, "rb") as f:
        return tomllib.load(f)


def get_dataset(name: str) -> dict:
    registry = load_registry()
    if name not in registry:
        available = ", ".join(registry)
        raise KeyError(f"Unknown dataset '{name}'. Available: {available}")
    return registry[name]
