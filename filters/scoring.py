def needs_llm(tender):
    score = tender.embedding_score
    if score >= 0.78:
        tender.llm_score = 1.0
        tender.relevant = True
        return False
    if score <= 0.35:
        tender.llm_score= 0.0
        tender.relevant = False
        return False
    return True

def calculate_final_score(tender):

    tender.final_score = (
        tender.embedding_score * 0.25
        +
        tender.llm_score * 0.65
        +
        tender.keyword_score * 0.10
    )

    tender.relevant = (
        tender.final_score >= 0.62
    )

    return tender