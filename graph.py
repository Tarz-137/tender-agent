from typing import Any, Literal

from langgraph.graph import END, StateGraph

from workflow_state import TenderState

from filters.scoring import needs_llm
from metrics_tracker import metrics_tracker

from nodes.metadata import metadata_node
from nodes.tender_nodes import (
    embedding_node,
    keyword_node,
    llm_node,
    scoring_node,
)

def route_after_metadata(
        state: TenderState,
) -> Literal["continue","stop"]:

    """
    Stop processing when metadata extraction failed
    """
    if state.get("error"):
        return "stop"

    if state.get("tender") is None:
        return "stop"

    return "continue"

def decide_llm_node(
        state: TenderState,
) -> dict[str,Any]:
    """
    Decide whether the tender needs LLM evaluation.

    needs_llm() also sets an automatic LLM score for 
    very high- or very low-confidence embeddings.

    """
    
    
    print("[DECISION] Checking whether LLM evaluation is needed")

    tender = state.get("tender") 

    if tender is None:
        return {
            "use_llm": False,
            "error":"Cannot use LLM without a tender",
        }   
    with metrics_tracker.track("decision"):
        use_LLM = needs_llm(tender)

    if use_LLM:
        print("[DECISION] Embedding score is uncertain; using LLM")
    else:
        print(
            "[DECISION] Embedding score is decisive; "
            "skipping LLM"
        )

    return  {
        "tender":tender,
        "use_llm":use_LLM
    }

def route_llm(
        state:TenderState,
) -> Literal["llm","score"]:
    """
    Send uncertain tenders to the LLM.
    High- and low-confidence tenders go directly to final scoring

    """
    if state.get("use_llm",False):
        return "llm"

    return "score"

def build_tender_graph():
    """
    Build a graph that processes one tender URL.

    Discovery and iteration over multiple URLs happen
    in main.py.
    """

    workflow = StateGraph(TenderState)

    workflow.add_node(
        "metadata",
        metadata_node,
    )

    workflow.add_node(
        "keyword",
        keyword_node
    )

    workflow.add_node(
        "embedding",
        embedding_node
    )

    workflow.add_node(
        "decide_llm",
        decide_llm_node,
    )

    workflow.add_node(
        "llm",
        llm_node
    )

    workflow.add_node(
        "score",
        scoring_node,
    )



    workflow.set_entry_point("metadata")

    workflow.add_conditional_edges(
        "metadata",
        route_after_metadata,
        {
            "continue":"keyword",
            "stop": END
        },
    )

    workflow.add_edge(
        "keyword",
        "embedding",
    )

    workflow.add_edge(
        "embedding",
        "decide_llm"
    )

    workflow.add_conditional_edges(
        "decide_llm",
        route_llm,
        {
            "llm":"llm",
            "score":"score",
        },
    )

    workflow.add_edge(
        "llm",
        "score"
    )
    # Persist evaluation in main before attempting document downloads.
    workflow.add_edge("score", END)

    return workflow.compile()
