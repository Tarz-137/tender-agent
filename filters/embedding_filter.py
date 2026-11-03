import numpy as np

from langchain_ollama import OllamaEmbeddings

from capabilities import RIPPLES_CAPABILITIES

from config import (
    OLLAMA_BASE_URL,
    OLLAMA_EMBEDDING_MODEL,
)

embeddings   = OllamaEmbeddings(
    model =OLLAMA_EMBEDDING_MODEL,
    base_url=OLLAMA_BASE_URL,
)


_COMPANY_VECTOR = None

def get_company_vector():
    global _COMPANY_VECTOR 
    if _COMPANY_VECTOR is None:
        _COMPANY_VECTOR = np.array(
            embeddings.embed_query(
                RIPPLES_CAPABILITIES
            )
        )
    return _COMPANY_VECTOR




def cosine_similarity(a,b):

    a = np.array(a)
    b = np.array(b)

    denominator = (
        np.linalg.norm(a)
        *
        np.linalg.norm(b)
    )

    if denominator == 0:
        return 0.0

    return float(
        np.dot(a,b)/denominator
    )

def embedding_filter(tender):
    tender_text = f"""
Title:
{tender.title}
Description:
{tender.description}
Organisation:
{tender.organisation}
Category:
{tender.product_category}
Location:
{tender.location}
"""
    vector = embeddings.embed_query(
        tender_text
    )
    tender.embedding_score = (
        cosine_similarity(
            get_company_vector(),
            vector
        )
    )

    return tender