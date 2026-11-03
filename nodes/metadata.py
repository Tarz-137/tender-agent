from typing import Any

from metrics_tracker import metrics_tracker
from workflow_state import TenderState

def metadata_node(state:TenderState) -> dict[str,Any]:
    """
    Extract normalized metadata for the current tender.

    Expected state:
    {
        "portal": <portal adapter instance>,
        "current_tender":{
            "tender_id":"...",
            "title":"...",
            "url":"..."
        }
    }

    The portal adapter must implement:
        extract_metadata(url:str) -> TenderMetadata

    Returns:{

        "tender":TenderMetadata,
        "error":None
    }
    """

    if state.get("tender") is not None:
        return {"tender": state["tender"], "error": None}

    portal = state["portal"]
    current_tender = state["current_tender"]

    try:
        tender_url = current_tender["url"]
        print(
            f"[METADATA] Extracting metadata for:"
            f"{current_tender.get('title',tender_url)}"
        )

        with metrics_tracker.track("metadata"):
            tender = portal.extract_metadata(
                tender_url
            )

        print(
            f"[METADATA] Success:   "
            f"{tender.tender_id or tender.title}"
        )

        return {
            "tender":tender,
            "error":None
        }
    except Exception as exc:
        print(
            f"[METADATA ERROR]"
            f"{current_tender.get('url','unknown')}: "
            f"{exc}"
        )

        return {
            "tender": None,
            "error" : str(exc)
        }
