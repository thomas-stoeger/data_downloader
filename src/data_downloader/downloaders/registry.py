from .base import BaseDownloader
from .figshare import FigshareDownloader
from .ftp import FtpDownloader
from .google_sheet import GoogleSheetDownloader
from .google_sheet_hashed import GoogleSheetHashedDownloader
from .harmonizome import HarmonizomeDownloader
from .nih_exporter import NihExporterDownloader
from .nlmcatalog import NlmCatalogDownloader
from .openalex import OpenAlexDownloader
from .opentargets import OpenTargetsDownloader
from .pubmed import PubmedDownloader
from .pubtator3 import Pubtator3Downloader
from .retractionwatch import RetractionWatchDownloader

_DOWNLOADERS: dict[str, type[BaseDownloader]] = {
    "ftp": FtpDownloader,
    "figshare": FigshareDownloader,
    "google_sheet": GoogleSheetDownloader,
    "google_sheet_hashed": GoogleSheetHashedDownloader,
    "harmonizome": HarmonizomeDownloader,
    "nih_exporter": NihExporterDownloader,
    "nlmcatalog": NlmCatalogDownloader,
    "openalex": OpenAlexDownloader,
    "opentargets": OpenTargetsDownloader,
    "pubmed": PubmedDownloader,
    "pubtator3": Pubtator3Downloader,
    "retractionwatch": RetractionWatchDownloader,
}


def get_downloader(downloader_type: str) -> BaseDownloader:
    if downloader_type not in _DOWNLOADERS:
        available = ", ".join(_DOWNLOADERS)
        raise KeyError(
            f"Unknown downloader '{downloader_type}'. Available: {available}"
        )
    return _DOWNLOADERS[downloader_type]()
