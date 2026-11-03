from capabilities import KEYWORDS

def keyword_filter(tender):

    text= " ".join([
        tender.title or "",
        tender.description or "",
        tender.organisation or "",
        tender.product_category or "",
    ]).lower()

    matches = []
    total_score =  0
    for keyword,weight in KEYWORDS.items():
        if keyword.lower() in text:
            matches.append(keyword)
            total_score += weight
    tender.matched_keywords = matches

    tender.keyword_score = min(
        total_score,
        1.0
    )

    return tender