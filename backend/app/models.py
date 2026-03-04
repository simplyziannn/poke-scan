from typing import Optional

from pydantic import BaseModel


class Candidate(BaseModel):
    card_id: str
    name: str
    set_name: Optional[str] = None
    number: Optional[str] = None
    confidence: float
    market_price_usd: Optional[float] = None
    grade_9_price_usd: Optional[float] = None
    psa_10_price_usd: Optional[float] = None
    price_note: Optional[str] = None
    price_source: Optional[str] = None
    price_source_url: Optional[str] = None
    price_updated_at: Optional[str] = None


class IdentifyResponse(BaseModel):
    extracted_number: Optional[str] = None
    extracted_name: Optional[str] = None
    raw_text: str
    candidates: list[Candidate]
