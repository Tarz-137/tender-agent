from typing import Optional
from pydantic import BaseModel,Field

class TenderDocument(BaseModel):
    name:str
    url:str
    document_type:Optional[str] = None
    size_kb: Optional[float] = None

#verify if dates are str or date
class TenderMetadata(BaseModel):
    portal:str
    tender_id:Optional[str] = None
    reference_number:Optional[str] = None
    title:str
    description: Optional[str] = None
    organisation: Optional[str] = None
    department: Optional[str]  = None
    location: Optional[str] = None
    pincode: Optional[str] = None
    tender_type: Optional[str] = None
    tender_category: Optional[str] = None
    product_category: Optional[str] = None
    tender_value: Optional[float] = None
    emd: Optional[float] =  None
    tender_fee: Optional[float] = None
    published_date: Optional[str] = None
    submission_deadline: Optional[str] = None
    bid_opening_date:Optional[str] = None
    source_url: str
    documents: list[TenderDocument] = Field(default_factory = list)
    matched_keywords: list[str] = Field(default_factory = list)
    keyword_score: float = 0.0
    embedding_score: float = 0.0
    llm_score:float = 0.0
    final_score: float = 0.0
    relevant: bool = False
    evaluation_cache_key: Optional[str] = None
