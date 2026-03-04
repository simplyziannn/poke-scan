import os

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from app.catalog import (
    debug_price_details,
    enrich_catalog_prices,
    get_catalog_meta,
    get_default_set_key,
    load_catalog,
    rebuild_catalog_from_number_search,
)
from app.match import match_cards
from app.models import IdentifyResponse
from app.ocr import run_ocr

app = FastAPI(title="Poke Scan API")
active_set_key = get_default_set_key()

default_origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://192.168.1.11:3000",
]
cors_env = os.getenv("CORS_ALLOW_ORIGINS", "")
allow_origins = [origin.strip() for origin in cors_env.split(",") if origin.strip()] or default_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_origin_regex=r"^https?://((localhost|127\.0\.0\.1)|((10|172|192)\.\d{1,3}\.\d{1,3}\.\d{1,3})|([a-zA-Z0-9-]+\.)?ngrok-free\.(app|dev)|([a-zA-Z0-9-]+\.)?up\.railway\.app)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@app.get("/catalog/status")
def catalog_status(set_key: str | None = None) -> dict[str, str | int]:
    selected_set_key = set_key or active_set_key
    cards = load_catalog(set_key=selected_set_key)
    meta = get_catalog_meta(set_key=selected_set_key)
    return {
        "set_key": str(meta.get("set_key") or selected_set_key),
        "cards_loaded": len(cards),
        "last_error": str(meta.get("last_error") or ""),
        "last_error_detail": str(meta.get("last_error_detail") or ""),
        "last_sync_source": str(meta.get("last_sync_source") or ""),
        "pages_fetched": int(meta.get("pages_fetched") or 0),
    }


@app.post("/catalog/rebuild")
def catalog_rebuild(set_key: str | None = None) -> dict[str, int | str]:
    selected_set_key = set_key or active_set_key
    cards = rebuild_catalog_from_number_search(set_key=selected_set_key)
    return {"set_key": selected_set_key, "cards_loaded": len(cards)}


@app.post("/catalog/enrich-prices")
def catalog_enrich_prices(
    set_key: str | None = None,
    max_cards: int | None = None,
    refresh_existing: bool = False,
) -> dict[str, int | str]:
    selected_set_key = set_key or active_set_key
    return enrich_catalog_prices(
        set_key=selected_set_key,
        max_cards=max_cards,
        refresh_existing=refresh_existing,
    )


@app.get("/catalog/debug-price")
def catalog_debug_price(source_url: str) -> dict[str, object]:
    return debug_price_details(source_url)


@app.post("/identify", response_model=IdentifyResponse)
async def identify(image: UploadFile = File(...)) -> IdentifyResponse:
    if not image.content_type or not image.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Uploaded file must be an image")

    payload = await image.read()
    if not payload:
        raise HTTPException(status_code=400, detail="Empty image payload")

    ocr_result = run_ocr(payload)
    candidates = match_cards(
        ocr_result.extracted_number,
        ocr_result.extracted_name,
        filename_hint=image.filename,
        set_key=active_set_key,
    )

    return IdentifyResponse(
        extracted_number=ocr_result.extracted_number,
        extracted_name=ocr_result.extracted_name,
        raw_text=ocr_result.raw_text,
        candidates=candidates,
    )
