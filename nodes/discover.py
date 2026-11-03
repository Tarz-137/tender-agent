from typing import Any
from metrics_tracker import metrics_tracker
from workflow_state import TenderState

def discover_tenders_node(state:TenderState) -> dict[str,Any]:
    """
    Discover tender detail URLS from the configured portal. 

    Expected state:
    {
        "portal": <portal adapter instance>,
        ...
    }

    Returns:
    {
        "tender_urls":[...]
    }
    """
    portal = state["portal"]

    try:
        with metrics_tracker.track("discovery"):
            tender_urls = portal.discover(
                limit=state.get("discovery_limit")
            )
        print(
            f"[DISCOVER] Found {len(tender_urls)} tender URLs"
        )
        return {
            "tender_urls":tender_urls,
            "error":None
        }
    except Exception as exc:
        print(
            f"[DISCOVER ERROR] {exc}"
        )
        return {
            "tender_urls":[],
            "error":str(exc)
        }

    
