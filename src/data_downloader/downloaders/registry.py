from .base import BaseDownloader
from .figshare import FigshareDownloader
from .ftp import FtpDownloader
from .harmonizome import HarmonizomeDownloader
from .nih_exporter import NihExporterDownloader
from .openalex import OpenAlexDownloader

_DOWNLOADERS: dict[str, type[BaseDownloader]] = {
    "ftp": FtpDownloader,
    "figshare": FigshareDownloader,
    "harmonizome": HarmonizomeDownloader,
    "nih_exporter": NihExporterDownloader,
    "openalex": OpenAlexDownloader,
}


def get_downloader(downloader_type: str) -> BaseDownloader:
    if downloader_type not in _DOWNLOADERS:
        available = ", ".join(_DOWNLOADERS)
        raise KeyError(
            f"Unknown downloader '{downloader_type}'. Available: {available}"
        )
    return _DOWNLOADERS[downloader_type]()
