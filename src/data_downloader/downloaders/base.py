from abc import ABC, abstractmethod
from pathlib import Path


class BaseDownloader(ABC):
    """
    All downloaders implement this interface.

    fetch()          — download dataset files into dest/, return list of paths written
    latest_version() — check the remote source and return a version string,
                       without downloading anything (used by 'dl check')
    """

    @abstractmethod
    def fetch(self, config: dict, dest: Path) -> list[Path]:
        """Download files into dest. dest is guaranteed to exist."""
        ...

    @abstractmethod
    def latest_version(self, config: dict) -> str:
        """Return a version string representing the current remote state."""
        ...
