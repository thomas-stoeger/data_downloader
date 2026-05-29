from .base import BaseDownloader
from .figshare import FigshareDownloader
from .ftp import FtpDownloader
from .harmonizome import HarmonizomeDownloader
from .nih_exporter import NihExporterDownloader
from .nlmcatalog import NlmCatalogDownloader
from .openalex import OpenAlexDownloader
from .pubmed import PubmedDownloader
from .pubtator3 import Pubtator3Downloader

_DOWNLOADERS: dict[str, type[BaseDownloader]] = {
    "ftp": FtpDownloader,
    "figshare": FigshareDownloader,
    "harmonizome": HarmonizomeDownloader,
    "nih_exporter": NihExporterDownloader,
    "nlmcatalog": NlmCatalogDownloader,
    "openalex": OpenAlexDownloader,
    "pubmed": PubmedDownloader,
    "pubtator3": Pubtator3Downloader,
}


def get_downloader(downloader_type: str) -> BaseDownloader:
    if downloader_type not in _DOWNLOADERS:
        available = ", ".join(_DOWNLOADERS)
        raise KeyError(
            f"Unknown downloader '{downloader_type}'. Available: {available}"
        )
    return _DOWNLOADERS[downloader_type]()
