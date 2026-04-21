"""
NIH ExPORTER downloader — all sections in one pass.

Year-based sections (projects, abstracts, publications, linktables):
    https://reporter.nih.gov/exporter/{section}/download/{year}
Single-file sections (patents, clinicalstudies):
    https://reporter.nih.gov/exporter/{section}/download
"""
import ssl
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

import certifi
from tqdm import tqdm

from .base import BaseDownloader

_BASE = "https://reporter.nih.gov/exporter"
_CHUNK = 1024 * 1024  # 1 MB
_SSL = ssl.create_default_context(cafile=certifi.where())

# section -> (earliest_year, zip_filename_pattern)
_YEAR_SECTIONS: dict[str, tuple[int, str]] = {
    "projects":     (1985, "RePORTER_PRJ_C_FY{year}.zip"),
    "abstracts":    (1985, "RePORTER_PRJABS_C_FY{year}.zip"),
    "publications": (1985, "RePORTER_PUB_C_FY{year}.zip"),
    "linktables":   (1985, "RePORTER_PUBLNK_C_FY{year}.zip"),
}

# section -> filename saved locally
_SINGLE_SECTIONS: dict[str, str] = {
    "patents":         "Patents.csv",
    "clinicalstudies": "ClinicalStudies.csv",
}


def _year_url(section: str, year: int) -> str:
    return f"{_BASE}/{section}/download/{year}"


def _single_url(section: str) -> str:
    return f"{_BASE}/{section}/download"


def _year_available(section: str, year: int) -> bool:
    try:
        with urllib.request.urlopen(_year_url(section, year), context=_SSL) as r:
            return r.status == 200
    except urllib.error.HTTPError:
        return False


def _latest_year(section: str) -> int:
    current = date.today().year
    for year in range(current, current - 3, -1):
        if _year_available(section, year):
            return year
    raise RuntimeError(f"Could not determine latest NIH ExPORTER year for section '{section}'")


def _download_year(section: str, year: int, filename: str, dest: Path) -> Path:
    local_path = dest / filename
    with urllib.request.urlopen(_year_url(section, year), context=_SSL) as response:
        size = int(response.headers.get("Content-Length", 0)) or None
        with (
            open(local_path, "wb") as f,
            tqdm(total=size, unit="B", unit_scale=True, desc=filename) as bar,
        ):
            while chunk := response.read(_CHUNK):
                f.write(chunk)
                bar.update(len(chunk))
    return local_path


def _download_single(section: str, filename: str, dest: Path) -> Path:
    local_path = dest / filename
    with urllib.request.urlopen(_single_url(section), context=_SSL) as response:
        size = int(response.headers.get("Content-Length", 0)) or None
        with (
            open(local_path, "wb") as f,
            tqdm(total=size, unit="B", unit_scale=True, desc=filename) as bar,
        ):
            while chunk := response.read(_CHUNK):
                f.write(chunk)
                bar.update(len(chunk))
    return local_path


class NihExporterDownloader(BaseDownloader):

    def latest_version(self, config: dict) -> str:
        return date.today().strftime("%Y-%m-%d")

    def fetch(self, config: dict, dest: Path) -> list[Path]:
        written = []

        for section, (earliest, pattern) in _YEAR_SECTIONS.items():
            end_year = _latest_year(section)
            for year in range(earliest, end_year + 1):
                if not _year_available(section, year):
                    continue
                filename = pattern.format(year=year)
                written.append(_download_year(section, year, filename, dest))

        for section, filename in _SINGLE_SECTIONS.items():
            written.append(_download_single(section, filename, dest))

        return written
