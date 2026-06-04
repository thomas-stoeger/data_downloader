from .alliancegenome import AllianceGenomeDownloader
from .base import BaseDownloader
from .ensembl_tsv import EnsemblTsvDownloader
from .figshare import FigshareDownloader
from .ftp import FtpDownloader
from .google_sheet import GoogleSheetDownloader
from .google_sheet_hashed import GoogleSheetHashedDownloader
from .gwas_catalog import GwasCatalogDownloader
from .harmonizome import HarmonizomeDownloader
from .hgnc import HgncDownloader
from .intact import IntActDownloader
from .interpro import InterProDownloader
from .mesh import MeshDownloader
from .nih_exporter import NihExporterDownloader
from .nlmcatalog import NlmCatalogDownloader
from .obo import OboDownloader
from .ols import OlsDownloader
from .openalex import OpenAlexDownloader
from .opentargets import OpenTargetsDownloader
from .proteinatlas import ProteinAtlasDownloader
from .pubmed import PubmedDownloader
from .pubtator3 import Pubtator3Downloader
from .retractionwatch import RetractionWatchDownloader
from .uniprot import UniProtDownloader
from .unknome import UnknomeDownloader
from .zenodo import ZenodoDownloader

_DOWNLOADERS: dict[str, type[BaseDownloader]] = {
    "ftp": FtpDownloader,
    "alliancegenome": AllianceGenomeDownloader,
    "ensembl_tsv": EnsemblTsvDownloader,
    "figshare": FigshareDownloader,
    "google_sheet": GoogleSheetDownloader,
    "google_sheet_hashed": GoogleSheetHashedDownloader,
    "gwas_catalog": GwasCatalogDownloader,
    "harmonizome": HarmonizomeDownloader,
    "hgnc": HgncDownloader,
    "intact": IntActDownloader,
    "interpro": InterProDownloader,
    "mesh": MeshDownloader,
    "nih_exporter": NihExporterDownloader,
    "nlmcatalog": NlmCatalogDownloader,
    "obo": OboDownloader,
    "ols": OlsDownloader,
    "openalex": OpenAlexDownloader,
    "opentargets": OpenTargetsDownloader,
    "proteinatlas": ProteinAtlasDownloader,
    "pubmed": PubmedDownloader,
    "pubtator3": Pubtator3Downloader,
    "retractionwatch": RetractionWatchDownloader,
    "uniprot": UniProtDownloader,
    "unknome": UnknomeDownloader,
    "zenodo": ZenodoDownloader,
}


def get_downloader(downloader_type: str) -> BaseDownloader:
    if downloader_type not in _DOWNLOADERS:
        available = ", ".join(_DOWNLOADERS)
        raise KeyError(
            f"Unknown downloader '{downloader_type}'. Available: {available}"
        )
    return _DOWNLOADERS[downloader_type]()
