from filters.keyword_filter import keyword_filter
from filters.embedding_filter import embedding_filter
from filters.llm_filter import llm_relevance_filter 
from filters.scoring import calculate_final_score
from metrics_tracker import metrics_tracker

def keyword_node(state):
    print("[KEYWORD] Evaluating keyword matches")

    with metrics_tracker.track("keyword"):
        tender = keyword_filter(
            state["tender"]
        )

    print(
        f"[KEYWORD] Score={tender.keyword_score:.3f}, "
        f"matches={len(tender.matched_keywords)}"
    )

    return {
        "tender":tender
    }
def embedding_node(state):
    print("[EMBEDDING] Generating and comparing embeddings")

    with metrics_tracker.track("embedding"):
        tender = embedding_filter(
            state["tender"]
        )

    print(
        f"[EMBEDDING] Score={tender.embedding_score:.3f}"
    )

    return {
        "tender":tender
    }
def llm_node(state):
    print("[LLM] Evaluating tender relevance")

    with metrics_tracker.track("llm"):
        tender = llm_relevance_filter(
            state["tender"]
        )

    print(
        f"[LLM] Score={tender.llm_score:.3f}"
    )

    return {
        "tender":tender
    }
def scoring_node(state):
    print("[SCORING] Calculating final score")

    with metrics_tracker.track("scoring"):
        tender = calculate_final_score(
            state["tender"]
        )

    print(
        f"[SCORING] Final score={tender.final_score:.3f}, "
        f"relevant={tender.relevant}"
    )

    return {
        "tender":tender
    }
