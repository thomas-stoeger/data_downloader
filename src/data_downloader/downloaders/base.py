from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable, TypeVar

_T = TypeVar("_T")

DOWNLOAD_RETRIES = 2


def _with_retries(fn: Callable[[], _T]) -> _T:
    last_exc: Exception | None = None
    for attempt in range(DOWNLOAD_RETRIES + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if attempt < DOWNLOAD_RETRIES:
                print(f"  attempt {attempt + 1} failed ({exc}), retrying...")
    raise last_exc  # type: ignore[misc]


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
