from __future__ import annotations

import os
import re
from typing import Optional

from rapidfuzz import fuzz

from app.catalog import (
    fetch_card_price_details,
    find_card_by_number_online,
    get_default_set_key,
    get_set_config,
    load_catalog,
)
from app.models import Candidate


def _collector_index(extracted_number: Optional[str]) -> Optional[str]:
    if not extracted_number:
        return None
    left = extracted_number.split("/", 1)[0].strip()
    return left.lstrip("0") or "0"


def _collector_index_candidates(extracted_number: Optional[str]) -> list[str]:
    if not extracted_number:
        return []

    left_raw = extracted_number.split("/", 1)[0].strip()
    digits = "".join(char for char in left_raw if char.isdigit())
    if not digits:
        return []

    candidates: list[str] = []

    def _push(value: str) -> None:
        normalized = value.lstrip("0") or "0"
        if normalized not in candidates:
            candidates.append(normalized)

    _push(digits)

    # OCR can prepend garbage digits (e.g. 703/080 instead of 54/080).
    if len(digits) >= 3:
        _push(digits[-2:])
        _push(digits[-3:])

    return candidates


def _collector_denominator(extracted_number: Optional[str]) -> Optional[str]:
    if not extracted_number or "/" not in extracted_number:
        return None
    right = extracted_number.split("/", 1)[1].strip()
    return right.zfill(3)


def _name_score(extracted_name: Optional[str], catalog_name: str) -> float:
    if not extracted_name:
        return 0.0

    token = fuzz.token_set_ratio(extracted_name, catalog_name) / 100.0
    partial = fuzz.partial_ratio(extracted_name, catalog_name) / 100.0
    ratio = fuzz.ratio(extracted_name, catalog_name) / 100.0
    composite = token * 0.55 + partial * 0.30 + ratio * 0.15
    return max(0.0, min(composite * 0.30, 0.30))


def _number_score(
    extracted_number: Optional[str],
    extracted_indexes: list[str],
    extracted_denominator: Optional[str],
    catalog_index: str,
    target_denominator: Optional[str],
) -> float:
    if not extracted_number or not extracted_indexes:
        return 0.0

    normalized_catalog = catalog_index.lstrip("0") or "0"
    if normalized_catalog in extracted_indexes:
        primary_index = extracted_indexes[0]
        if primary_index == normalized_catalog:
            # Strong signal even if denominator OCR is noisy.
            if target_denominator and extracted_denominator == target_denominator:
                return 0.78
            return 0.62
        # Recovered from noisy leading digits; still a strong signal.
        if target_denominator and extracted_denominator == target_denominator:
            return 0.70
        return 0.55

    return 0.0


def _normalize_filename_hint(filename_hint: Optional[str]) -> Optional[str]:
    if not filename_hint:
        return None
    base = filename_hint.rsplit("/", 1)[-1]
    base = base.rsplit(".", 1)[0]
    normalized = re.sub(r"[_\-]+", " ", base).strip()
    normalized = re.sub(r"\s+", " ", normalized)
    if len(normalized) < 3:
        return None
    return normalized


def _is_low_quality_name(name: Optional[str]) -> bool:
    if not name:
        return True
    cleaned = name.strip()
    if len(cleaned) < 3:
        return True
    alpha_count = sum(char.isalpha() for char in cleaned)
    return alpha_count < 3


def match_cards(
    extracted_number: Optional[str],
    extracted_name: Optional[str],
    filename_hint: Optional[str] = None,
    set_key: Optional[str] = None,
) -> list[Candidate]:
    selected_set_key = (set_key or os.getenv("POKE_SCAN_SET_KEY") or get_default_set_key()).strip().lower()
    set_config = get_set_config(selected_set_key)
    cards = load_catalog(set_key=set_config.key)

    extracted_index = _collector_index(extracted_number)
    extracted_indexes = _collector_index_candidates(extracted_number)
    extracted_denominator = _collector_denominator(extracted_number)
    fallback_name = _normalize_filename_hint(filename_hint)
    effective_name = fallback_name if _is_low_quality_name(extracted_name) else extracted_name
    if not cards and extracted_index:
        fallback = find_card_by_number_online(extracted_index, set_key=set_config.key)
        if fallback:
            cards = [fallback]

    if not cards:
        return []

    candidates: list[Candidate] = []

    for card in cards:
        confidence = _number_score(
            extracted_number,
            extracted_indexes,
            extracted_denominator,
            card.number_index,
            set_config.collector_denominator,
        )
        confidence += _name_score(effective_name, card.name)

        if confidence < 0.09:
            continue

        candidates.append(
            Candidate(
                card_id=card.card_id,
                name=card.name,
                set_name=card.set_name,
                number=card.number,
                confidence=round(min(confidence, 0.99), 3),
                market_price_usd=card.market_price_usd,
                grade_9_price_usd=card.grade_9_price_usd,
                psa_10_price_usd=card.psa_10_price_usd,
                price_note="Ungraded market price",
                price_source="PriceCharting",
                price_source_url=card.source_url,
            )
        )

    ranked = sorted(candidates, key=lambda item: item.confidence, reverse=True)

    # If the synced catalog is partial or misses a specific number, resolve directly from online search.
    if extracted_index:
        needs_online_lookup = not ranked or ranked[0].confidence < 0.74
        if needs_online_lookup:
            fallback = find_card_by_number_online(extracted_index, set_key=set_config.key)
            if fallback:
                fallback_confidence = 0.90
                if effective_name:
                    fallback_confidence = max(
                        0.90,
                        min(0.99, 0.78 + _name_score(effective_name, fallback.name)),
                    )
                return [
                    Candidate(
                        card_id=fallback.card_id,
                        name=fallback.name,
                        set_name=fallback.set_name,
                        number=fallback.number,
                        confidence=round(fallback_confidence, 3),
                        market_price_usd=fallback.market_price_usd,
                        grade_9_price_usd=fallback.grade_9_price_usd,
                        psa_10_price_usd=fallback.psa_10_price_usd,
                        price_note="Ungraded market price",
                        price_source="PriceCharting",
                        price_source_url=fallback.source_url,
                    )
                ]

    if not extracted_number and not effective_name and ranked and ranked[0].confidence < 0.16:
        return []

    if ranked and ranked[0].price_source_url:
        details = fetch_card_price_details(ranked[0].price_source_url)
        if details.get("ungraded") is not None:
            ranked[0].market_price_usd = details.get("ungraded")
        ranked[0].grade_9_price_usd = details.get("grade_9")
        ranked[0].psa_10_price_usd = details.get("psa_10")

    return ranked[:3]
