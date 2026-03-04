from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_CACHE_TTL_SECONDS = 60 * 60 * 6
_PRICE_DETAIL_CACHE_TTL_SECONDS = 60 * 30
_DATA_DIR = Path(__file__).resolve().parent / "data"


@dataclass(frozen=True)
class SetConfig:
    key: str
    set_name: str
    set_url: str
    slug_path: str
    cache_filename: str
    set_max_index: int
    collector_denominator: Optional[str]
    search_query_base: str


SET_CONFIGS: dict[str, SetConfig] = {
    "nihil-zero": SetConfig(
        key="nihil-zero",
        set_name="Pokemon Japanese Nihil Zero",
        set_url="https://www.pricecharting.com/console/pokemon-japanese-nihil-zero",
        slug_path="pokemon-japanese-nihil-zero",
        cache_filename="nihil_zero_cache.json",
        set_max_index=120,
        collector_denominator="080",
        search_query_base="pokemon japanese nihil zero",
    ),
    "inferno-x": SetConfig(
        key="inferno-x",
        set_name="Pokemon Japanese Inferno X",
        set_url="https://www.pricecharting.com/console/pokemon-japanese-inferno-x",
        slug_path="pokemon-japanese-inferno-x",
        cache_filename="inferno_x_cache.json",
        set_max_index=200,
        collector_denominator=None,
        search_query_base="pokemon japanese inferno x",
    ),
}


@dataclass
class CatalogCard:
    card_id: str
    name: str
    set_name: str
    number_index: str
    number: Optional[str]
    market_price_usd: Optional[float]
    grade_9_price_usd: Optional[float]
    psa_10_price_usd: Optional[float]
    source_url: str


_CATALOG_CACHE: dict[str, dict[str, object]] = {}
_CATALOG_META: dict[str, dict[str, object]] = {}
_PRICE_DETAIL_CACHE: dict[str, tuple[float, dict[str, Optional[float]]]] = {}

_NUM_IN_NAME_PATTERN = re.compile(r"#\s*(\d{1,3})")
_NUM_IN_HREF_PATTERN = re.compile(r"-(\d{1,3})(?:$|[/?#])")
_PRICE_PATTERN = re.compile(r"\$(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)")


def list_supported_sets() -> list[str]:
    return sorted(SET_CONFIGS.keys())


def get_default_set_key() -> str:
    configured = (os.getenv("POKE_SCAN_SET_KEY") or "nihil-zero").strip().lower()
    if configured in SET_CONFIGS:
        return configured
    return "nihil-zero"


def get_set_config(set_key: Optional[str] = None) -> SetConfig:
    key = (set_key or get_default_set_key()).strip().lower()
    config = SET_CONFIGS.get(key)
    if config:
        return config
    return SET_CONFIGS[get_default_set_key()]


def _cache_path(config: SetConfig) -> Path:
    return _DATA_DIR / config.cache_filename


def _state_for(config: SetConfig) -> tuple[dict[str, object], dict[str, object]]:
    cache_state = _CATALOG_CACHE.setdefault(config.key, {"fetched_at": 0.0, "cards": []})
    meta_state = _CATALOG_META.setdefault(
        config.key,
        {
            "last_error": None,
            "last_error_detail": None,
            "last_sync_source": None,
            "pages_fetched": 0,
            "set_key": config.key,
        },
    )
    return cache_state, meta_state


def _parse_price(text: str) -> Optional[float]:
    match = _PRICE_PATTERN.search(text)
    if not match:
        return None
    return float(match.group(1).replace(",", ""))


def _sanitize_price_triplet(
    ungraded: Optional[float], grade_9: Optional[float], psa_10: Optional[float]
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    # Guard against parser drift where graded values get parsed as Ungraded.
    if grade_9 is not None and ungraded is not None and abs(grade_9 - ungraded) < 0.001:
        grade_9 = None
    if (
        psa_10 is not None
        and ungraded is not None
        and abs(psa_10 - ungraded) < 0.001
    ):
        psa_10 = None
    return ungraded, grade_9, psa_10


def _card_id_from_href(config: SetConfig, href: str) -> str:
    slug = href.rsplit("/", 1)[-1]
    return f"{config.key}-{slug}"


def _fetch_html(url: str) -> str:
    request_headers = {
        "User-Agent": "Mozilla/5.0 (compatible; poke-scan/0.1; +http://localhost)",
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        request = Request(url=url, headers=request_headers)
        with urlopen(request, timeout=10) as response:
            return response.read().decode("utf-8", errors="ignore")
    except (URLError, HTTPError, TimeoutError) as exc:
        if "pricecharting.com" not in url:
            raise URLError(str(exc)) from exc

        try:
            return _fetch_html_mirror(url, user_agent=request_headers["User-Agent"])
        except (URLError, HTTPError, TimeoutError) as mirror_exc:
            raise URLError(f"{exc}; mirror_failed={mirror_exc}") from mirror_exc


def _fetch_html_mirror(url: str, user_agent: str = "Mozilla/5.0 (compatible; poke-scan/0.1; +http://localhost)") -> str:
    mirror = url.replace("https://", "https://r.jina.ai/http://", 1)
    mirror_headers = {
        "User-Agent": user_agent,
        "Accept": "text/plain,text/html;q=0.9,*/*;q=0.8",
    }
    mirror_request = Request(url=mirror, headers=mirror_headers)
    with urlopen(mirror_request, timeout=20) as response:
        return response.read().decode("utf-8", errors="ignore")


def _parse_number_index(name: str, href: str) -> Optional[str]:
    name_match = _NUM_IN_NAME_PATTERN.search(name)
    if name_match:
        return str(int(name_match.group(1)))

    href_match = _NUM_IN_HREF_PATTERN.search(href)
    if href_match:
        return str(int(href_match.group(1)))

    return None


def _card_anchor_pattern(config: SetConfig) -> re.Pattern[str]:
    return re.compile(
        rf'href="(?P<href>/game/{re.escape(config.slug_path)}/[^"]+)">\s*(?P<name>[^<]+?)\s*</a>',
        re.IGNORECASE,
    )


def _mirror_card_pattern(config: SetConfig) -> re.Pattern[str]:
    return re.compile(
        rf"\[(?P<name>[^\]]+?)\s*#(?P<number>\d{{1,3}})\]\((?P<url>https://www\\.pricecharting\\.com/game/{re.escape(config.slug_path)}/[^\)]+)\)",
        re.IGNORECASE,
    )


def _parse_cards(html: str, config: SetConfig) -> list[CatalogCard]:
    cards: list[CatalogCard] = []

    for match in _card_anchor_pattern(config).finditer(html):
        href = match.group("href")
        label = re.sub(r"\s+", " ", match.group("name")).strip()

        number_index = _parse_number_index(label, href)
        if not number_index:
            continue

        clean_name = re.sub(r"\s*#\s*\d{1,3}\s*$", "", label).strip()
        full_url = f"https://www.pricecharting.com{href}"

        snippet = html[match.end() : match.end() + 260]
        price = _parse_price(snippet)

        cards.append(
            CatalogCard(
                card_id=_card_id_from_href(config, href),
                name=clean_name or label,
                set_name=config.set_name,
                number_index=number_index,
                number=number_index,
                market_price_usd=price,
                grade_9_price_usd=None,
                psa_10_price_usd=None,
                source_url=full_url,
            )
        )

    for match in _mirror_card_pattern(config).finditer(html):
        label = re.sub(r"\s+", " ", match.group("name")).strip()
        number_index = str(int(match.group("number")))
        full_url = match.group("url")
        href = "/" + full_url.split("pricecharting.com/", 1)[1]
        snippet = html[match.end() : match.end() + 220]
        price = _parse_price(snippet)

        cards.append(
            CatalogCard(
                card_id=_card_id_from_href(config, href),
                name=label,
                set_name=config.set_name,
                number_index=number_index,
                number=number_index,
                market_price_usd=price,
                grade_9_price_usd=None,
                psa_10_price_usd=None,
                source_url=full_url,
            )
        )

    unique: dict[str, CatalogCard] = {}
    for card in cards:
        existing = unique.get(card.card_id)
        if not existing:
            unique[card.card_id] = card
            continue

        if existing.market_price_usd is None and card.market_price_usd is not None:
            unique[card.card_id] = card

    return list(unique.values())


def _fetch_set_pages(config: SetConfig) -> tuple[list[CatalogCard], int]:
    all_cards: dict[str, CatalogCard] = {}
    pages_fetched = 0

    for page in range(1, 8):
        url = config.set_url if page == 1 else f"{config.set_url}?page={page}"
        html = _fetch_html(url)
        page_cards = _parse_cards(html, config)
        pages_fetched += 1

        if not page_cards and page > 2:
            break

        new_added = 0
        for card in page_cards:
            if card.card_id not in all_cards:
                all_cards[card.card_id] = card
                new_added += 1
            elif all_cards[card.card_id].market_price_usd is None and card.market_price_usd is not None:
                all_cards[card.card_id] = card

        if page > 2 and new_added == 0:
            break

    return list(all_cards.values()), pages_fetched


def _persist_cards(config: SetConfig, cards: list[CatalogCard]) -> None:
    path = _cache_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            "card_id": card.card_id,
            "name": card.name,
            "set_name": card.set_name,
            "number_index": card.number_index,
            "number": card.number,
            "market_price_usd": card.market_price_usd,
            "grade_9_price_usd": card.grade_9_price_usd,
            "psa_10_price_usd": card.psa_10_price_usd,
            "source_url": card.source_url,
        }
        for card in cards
    ]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_persisted_cards(config: SetConfig) -> list[CatalogCard]:
    path = _cache_path(config)
    if not path.exists():
        return []

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    cards: list[CatalogCard] = []
    for item in payload:
        ungraded, grade_9, psa_10 = _sanitize_price_triplet(
            item.get("market_price_usd"),
            item.get("grade_9_price_usd"),
            item.get("psa_10_price_usd"),
        )
        cards.append(
            CatalogCard(
                card_id=str(item.get("card_id", "")),
                name=str(item.get("name", "")),
                set_name=str(item.get("set_name", config.set_name)),
                number_index=str(item.get("number_index", "")),
                number=item.get("number"),
                market_price_usd=ungraded,
                grade_9_price_usd=grade_9,
                psa_10_price_usd=psa_10,
                source_url=str(item.get("source_url", "")),
            )
        )
    return [card for card in cards if card.card_id and card.name and card.number_index]


def _merge_cards(primary: list[CatalogCard], secondary: list[CatalogCard]) -> list[CatalogCard]:
    merged: dict[str, CatalogCard] = {}

    for card in secondary:
        merged[card.card_id] = card

    for card in primary:
        existing = merged.get(card.card_id)
        if not existing:
            merged[card.card_id] = card
            continue
        if existing.market_price_usd is None and card.market_price_usd is not None:
            merged[card.card_id] = card

    return list(merged.values())


def load_catalog(set_key: Optional[str] = None, force_refresh: bool = False) -> list[CatalogCard]:
    config = get_set_config(set_key)
    cache_state, meta_state = _state_for(config)

    now = time.time()
    cached_at = float(cache_state["fetched_at"])
    cached_cards = cache_state["cards"]

    if not force_refresh and cached_cards and now - cached_at < _CACHE_TTL_SECONDS:
        return list(cached_cards)  # type: ignore[return-value]

    try:
        parsed_cards, pages_fetched = _fetch_set_pages(config)
        if parsed_cards:
            persisted_cards = _load_persisted_cards(config)
            merged_cards = _merge_cards(parsed_cards, persisted_cards)
            cache_state["fetched_at"] = now
            cache_state["cards"] = merged_cards
            _persist_cards(config, merged_cards)
            meta_state["last_error"] = None
            meta_state["last_error_detail"] = None
            meta_state["last_sync_source"] = "live_set_page"
            meta_state["pages_fetched"] = pages_fetched
            return merged_cards
    except (URLError, HTTPError, TimeoutError) as exc:
        meta_state["last_error"] = "network_error_set_page"
        meta_state["last_error_detail"] = str(exc)

    persisted = _load_persisted_cards(config)
    if persisted:
        cache_state["fetched_at"] = now
        cache_state["cards"] = persisted
        meta_state["last_sync_source"] = "local_cache_file"
        return persisted

    return list(cached_cards) if cached_cards else []  # type: ignore[return-value]


def get_catalog_meta(set_key: Optional[str] = None) -> dict[str, object]:
    config = get_set_config(set_key)
    cache_state, meta_state = _state_for(config)
    return {
        "set_key": config.key,
        "cards_loaded": len(cache_state.get("cards", [])),
        "fetched_at": cache_state.get("fetched_at"),
        "last_error": meta_state.get("last_error"),
        "last_error_detail": meta_state.get("last_error_detail"),
        "last_sync_source": meta_state.get("last_sync_source"),
        "pages_fetched": meta_state.get("pages_fetched"),
        "cache_file": str(_cache_path(config)),
    }


def _parse_card_link_from_search(html: str, number_index: str, config: SetConfig) -> Optional[tuple[str, str]]:
    pattern = re.compile(
        rf'href="(?P<href>/game/{re.escape(config.slug_path)}/[^"]+)">(?P<name>[^<]*?)\s*#0*{re.escape(number_index)}\s*</a>',
        re.IGNORECASE,
    )
    match = pattern.search(html)
    if match:
        href = match.group("href")
        name = re.sub(r"\s+", " ", match.group("name")).strip()
        return href, name

    generic = re.compile(
        rf'href="(?P<href>/game/{re.escape(config.slug_path)}/[^"]+)">(?P<name>[^<]*?)</a>',
        re.IGNORECASE,
    )
    target_number = str(int(number_index))
    for alt in generic.finditer(html):
        href = alt.group("href")
        inferred = _parse_number_index(alt.group("name"), href)
        if inferred and inferred == target_number:
            name = re.sub(r"\s+", " ", alt.group("name")).strip()
            return href, name

    return None


def _extract_price_from_label_value_cell(html: str, label_pattern: str) -> Optional[float]:
    pattern = re.compile(
        rf"{label_pattern}(?:(?!</tr>).){{0,400}}?<(?P<tag>td|div|span)[^>]*>\s*(?P<value>[^<]{{1,40}})\s*</(?P=tag)>",
        re.IGNORECASE | re.DOTALL,
    )
    for match in pattern.finditer(html):
        value = re.sub(r"\s+", " ", match.group("value")).strip()
        if value in {"-", "--", "N/A", "n/a", "none", "None"}:
            return None
        price = _parse_price(value)
        if price is not None:
            return price
    return None


def _extract_row_scoped_price(html: str, label_pattern: str) -> Optional[float]:
    pattern = re.compile(
        rf"{label_pattern}(?:(?!</tr>).){{0,500}}?\$(\d{{1,3}}(?:,\d{{3}})*(?:\.\d{{2}})?)",
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(html)
    if not match:
        return None
    return float(match.group(1).replace(",", ""))


def _extract_label_price_fallback(html: str, label_pattern: str) -> Optional[float]:
    # Fallback parser for plain-text mirror responses and HTML layouts where table structure differs.
    pattern = re.compile(
        rf"{label_pattern}[^\$]{{0,1400}}?\$(\d{{1,3}}(?:,\d{{3}})*(?:\.\d{{2}})?)",
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(html)
    if not match:
        return None
    return float(match.group(1).replace(",", ""))


def _extract_full_guide_pipe_price(html: str, label_pattern: str) -> Optional[float]:
    # Handles text/table mirror layout: "Grade 9  | $352.67" or "PSA 10: $1,350.07"
    pattern = re.compile(
        rf"{label_pattern}\s*(?:\||:)\s*\$(\d{{1,3}}(?:,\d{{3}})*(?:\.\d{{2}})?)",
        re.IGNORECASE,
    )
    match = pattern.search(html)
    if not match:
        return None
    return float(match.group(1).replace(",", ""))


def _extract_full_price_guide_line_price(html: str, label_pattern: str) -> Optional[float]:
    # Prefer the "Full Price Guide" section where each grade appears as "Grade 9 $19.38".
    section_match = re.search(r"Full\s+Price\s+Guide", html, flags=re.IGNORECASE)
    scope = html[section_match.start() : section_match.start() + 12000] if section_match else html
    pattern = re.compile(
        rf"{label_pattern}\s+\$(\d{{1,3}}(?:,\d{{3}})*(?:\.\d{{2}})?)",
        re.IGNORECASE,
    )
    match = pattern.search(scope)
    if not match:
        return None
    return float(match.group(1).replace(",", ""))


def _extract_compare_row_prices(html: str) -> dict[str, Optional[float]]:
    # Parse the common compare-prices layout where labels are one row and values are the next row.
    # Example labels: Ungraded | Grade 7 | Grade 8 | Grade 9 | Grade 9.5 | PSA 10
    # Example values: $6.28 | - | - | $17.95 | $20.00 | $60.30
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)

    header = re.search(
        r"Ungraded\s+Grade\s*7\s+Grade\s*8\s+Grade\s*9(?:\s+Grade\s*9\.?5)?\s+PSA\s*10",
        text,
        flags=re.IGNORECASE,
    )
    if not header:
        return {"ungraded": None, "grade_9": None, "psa_10": None}

    tail = text[header.end() : header.end() + 2500]
    token_pattern = re.compile(
        r"(?<![+\-])\$(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)|(?<![+\-])-(?![\d$])"
    )
    tokens: list[Optional[float]] = []
    for match in token_pattern.finditer(tail):
        if match.group(1):
            tokens.append(float(match.group(1).replace(",", "")))
        else:
            tokens.append(None)
        if len(tokens) >= 6:
            break

    if len(tokens) < 6:
        return {"ungraded": None, "grade_9": None, "psa_10": None}

    return {
        "ungraded": tokens[0],
        "grade_9": tokens[3],
        "psa_10": tokens[5],
    }


def _parse_ungraded_from_card_page(html: str) -> Optional[float]:
    compare = _extract_compare_row_prices(html).get("ungraded")
    if compare is not None:
        return compare
    return (
        _extract_full_price_guide_line_price(html, r"Ungraded")
        or _extract_price_from_label_value_cell(html, r"Ungraded")
        or _extract_row_scoped_price(html, r"Ungraded")
        or _extract_label_price_fallback(html, r"Ungraded")
        or _extract_full_guide_pipe_price(html, r"Ungraded")
    )


def _parse_grade9_from_card_page(html: str) -> Optional[float]:
    compare = _extract_compare_row_prices(html).get("grade_9")
    if compare is not None:
        return compare
    return (
        _extract_full_price_guide_line_price(html, r"Grade\s*9(?!\s*\.?5)")
        or _extract_price_from_label_value_cell(html, r"Grade\s*9(?!\s*\.?5)")
        or _extract_row_scoped_price(html, r"Grade\s*9(?!\s*\.?5)")
        or _extract_label_price_fallback(html, r"Grade\s*9(?!\s*\.?5)")
        or _extract_full_guide_pipe_price(html, r"Grade\s*9(?!\s*\.?5)")
    )


def _parse_psa10_from_card_page(html: str) -> Optional[float]:
    compare = _extract_compare_row_prices(html).get("psa_10")
    if compare is not None:
        return compare
    return (
        _extract_full_price_guide_line_price(html, r"PSA\s*10")
        or _extract_price_from_label_value_cell(html, r"PSA\s*10")
        or _extract_row_scoped_price(html, r"PSA\s*10")
        or _extract_label_price_fallback(html, r"PSA\s*10")
        or _extract_full_guide_pipe_price(html, r"PSA\s*10")
    )


def fetch_card_price_details(source_url: str, force_refresh: bool = False) -> dict[str, Optional[float]]:
    now = time.time()
    cached = _PRICE_DETAIL_CACHE.get(source_url)
    if not force_refresh and cached and now - cached[0] < _PRICE_DETAIL_CACHE_TTL_SECONDS:
        return cached[1]

    try:
        html = _fetch_html(source_url)
    except (URLError, HTTPError, TimeoutError):
        details = {"ungraded": None, "grade_9": None, "psa_10": None}
        # Do not cache full-miss network failures for long; allow next enrichment retry.
        if not force_refresh:
            _PRICE_DETAIL_CACHE[source_url] = (now, details)
        return details

    ungraded, grade_9, psa_10 = _sanitize_price_triplet(
        _parse_ungraded_from_card_page(html),
        _parse_grade9_from_card_page(html),
        _parse_psa10_from_card_page(html),
    )

    # Direct fetch can return a reduced page that omits graded rows.
    # Retry via text mirror when graded values are missing.
    if grade_9 is None and psa_10 is None and "pricecharting.com" in source_url:
        try:
            mirror_html = _fetch_html_mirror(source_url)
            mirror_ungraded, mirror_grade_9, mirror_psa_10 = _sanitize_price_triplet(
                _parse_ungraded_from_card_page(mirror_html),
                _parse_grade9_from_card_page(mirror_html),
                _parse_psa10_from_card_page(mirror_html),
            )
            if mirror_ungraded is not None:
                ungraded = mirror_ungraded
            if mirror_grade_9 is not None:
                grade_9 = mirror_grade_9
            if mirror_psa_10 is not None:
                psa_10 = mirror_psa_10
        except (URLError, HTTPError, TimeoutError):
            pass

    details = {"ungraded": ungraded, "grade_9": grade_9, "psa_10": psa_10}
    # Avoid sticky negative cache entries where all fields are missing.
    if any(value is not None for value in details.values()):
        _PRICE_DETAIL_CACHE[source_url] = (now, details)
    return details


def find_card_by_number_online(number_index: str, set_key: Optional[str] = None) -> Optional[CatalogCard]:
    config = get_set_config(set_key)
    _cache_state, meta_state = _state_for(config)

    number_plain = str(int(number_index))
    number_padded = number_plain.zfill(3)
    queries = [
        f"{config.search_query_base} #{number_plain}",
        f"{config.search_query_base} #{number_padded}",
        f"{config.search_query_base} {number_plain}",
        f"{config.search_query_base} {number_padded}",
    ]

    card_link: Optional[tuple[str, str]] = None
    last_exc: Optional[Exception] = None
    for query in queries:
        query_url = "https://www.pricecharting.com/search-products?type=pokemon-cards&q=" + query.replace(" ", "+")
        try:
            search_html = _fetch_html(query_url)
        except (URLError, HTTPError, TimeoutError) as exc:
            last_exc = exc
            continue
        card_link = _parse_card_link_from_search(search_html, number_plain, config)
        if card_link:
            break

    if not card_link and last_exc:
        meta_state["last_error"] = "network_error_search_page"
        meta_state["last_error_detail"] = str(last_exc)
        return None

    if not card_link:
        meta_state["last_error"] = "search_no_matching_card_link"
        meta_state["last_error_detail"] = f"number_index={number_plain}"
        return None

    href, name = card_link
    full_url = f"https://www.pricecharting.com{href}"

    try:
        card_html = _fetch_html(full_url)
    except (URLError, HTTPError, TimeoutError) as exc:
        meta_state["last_error"] = "network_error_card_page"
        meta_state["last_error_detail"] = str(exc)
        return None

    ungraded = _parse_ungraded_from_card_page(card_html)
    grade_9 = _parse_grade9_from_card_page(card_html)
    psa_10 = _parse_psa10_from_card_page(card_html)
    ungraded, grade_9, psa_10 = _sanitize_price_triplet(ungraded, grade_9, psa_10)
    _PRICE_DETAIL_CACHE[full_url] = (
        time.time(),
        {"ungraded": ungraded, "grade_9": grade_9, "psa_10": psa_10},
    )
    meta_state["last_error"] = None
    meta_state["last_error_detail"] = None
    meta_state["last_sync_source"] = "online_number_fallback"
    return CatalogCard(
        card_id=_card_id_from_href(config, href),
        name=name,
        set_name=config.set_name,
        number_index=number_plain,
        number=number_plain,
        market_price_usd=ungraded,
        grade_9_price_usd=grade_9,
        psa_10_price_usd=psa_10,
        source_url=full_url,
    )


def rebuild_catalog_from_number_search(
    start: int = 1,
    end: Optional[int] = None,
    set_key: Optional[str] = None,
) -> list[CatalogCard]:
    config = get_set_config(set_key)
    cache_state, meta_state = _state_for(config)
    upper = end if end is not None else config.set_max_index

    existing = {card.card_id: card for card in load_catalog(set_key=config.key, force_refresh=True)}
    rebuilt: dict[str, CatalogCard] = dict(existing)

    for number in range(start, upper + 1):
        online = find_card_by_number_online(str(number), set_key=config.key)
        if not online:
            continue
        current = rebuilt.get(online.card_id)
        if not current:
            rebuilt[online.card_id] = online
            continue
        if current.market_price_usd is None and online.market_price_usd is not None:
            rebuilt[online.card_id] = online

    cards = list(rebuilt.values())
    if cards:
        now = time.time()
        cache_state["fetched_at"] = now
        cache_state["cards"] = cards
        _persist_cards(config, cards)
        meta_state["last_error"] = None
        meta_state["last_error_detail"] = None
        meta_state["last_sync_source"] = "number_search_rebuild"
    return cards


def enrich_catalog_prices(
    set_key: Optional[str] = None,
    max_cards: Optional[int] = None,
    refresh_existing: bool = False,
) -> dict[str, int | str]:
    config = get_set_config(set_key)
    cache_state, meta_state = _state_for(config)
    cards = load_catalog(set_key=config.key, force_refresh=False)
    if not cards:
        return {"set_key": config.key, "checked": 0, "updated": 0}

    updated = 0
    checked = 0
    mutable = list(cards)

    for index, card in enumerate(mutable):
        if max_cards is not None and checked >= max_cards:
            break
        needs_update = refresh_existing or card.grade_9_price_usd is None or card.psa_10_price_usd is None
        if not needs_update:
            continue
        checked += 1
        details = fetch_card_price_details(card.source_url, force_refresh=refresh_existing)
        ungraded, grade_9, psa_10 = _sanitize_price_triplet(
            details.get("ungraded") if details.get("ungraded") is not None else card.market_price_usd,
            details.get("grade_9") if details.get("grade_9") is not None else card.grade_9_price_usd,
            details.get("psa_10") if details.get("psa_10") is not None else card.psa_10_price_usd,
        )
        if (
            ungraded == card.market_price_usd
            and grade_9 == card.grade_9_price_usd
            and psa_10 == card.psa_10_price_usd
        ):
            continue
        mutable[index] = CatalogCard(
            card_id=card.card_id,
            name=card.name,
            set_name=card.set_name,
            number_index=card.number_index,
            number=card.number,
            market_price_usd=ungraded,
            grade_9_price_usd=grade_9,
            psa_10_price_usd=psa_10,
            source_url=card.source_url,
        )
        updated += 1

    if updated > 0:
        now = time.time()
        cache_state["fetched_at"] = now
        cache_state["cards"] = mutable
        _persist_cards(config, mutable)
        meta_state["last_sync_source"] = "price_enrichment"
        meta_state["last_error"] = None
        meta_state["last_error_detail"] = None

    return {"set_key": config.key, "checked": checked, "updated": updated}


# Backward-compatible wrappers for existing imports.
def load_nihil_zero_catalog(force_refresh: bool = False) -> list[CatalogCard]:
    return load_catalog(set_key="nihil-zero", force_refresh=force_refresh)
