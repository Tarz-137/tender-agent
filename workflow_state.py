from typing import Any, TypedDict

from models.tender import TenderMetadata
from portals.base import TenderPortal


class TenderState(TypedDict, total=False):
    portal: TenderPortal
    discovery_limit: int | None
    tender_urls: list[str]
    current_tender: dict[str, Any]

    tender: TenderMetadata | None
    use_llm: bool
    error: str | None
