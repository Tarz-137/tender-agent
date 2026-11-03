from abc import ABC, abstractmethod
from pathlib import Path

from models.tender import TenderMetadata


class TenderPortal(ABC):
    @abstractmethod
    def discover(self, limit: int | None = None) -> list[str]:
        """Return tender detail URLs."""
        raise NotImplementedError

    @abstractmethod
    def extract_metadata(self, url: str) -> TenderMetadata:
        """Extract metadata without downloading tender files."""
        raise NotImplementedError

    @abstractmethod
    def download_document(self, url: str, destination: Path) -> None:
        """Download one tender document using the portal session."""
        raise NotImplementedError
