from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

SET_URL = "https://www.pricecharting.com/console/pokemon-japanese-nihil-zero"
_CACHE_TTL_SECONDS = 60 * 60 * 6
_CACHE: dict[str, object] = {"fetched_at": 0.0, "cards": []}
_PERSIST_PATH = Path(__file__).resolve().parent / "data" / "nihil_zero_cache.json"
_META: dict[str, object] = {
    "last_error": None,
    "last_error_detail": None,
    "last_sync_source": None,
    "pages_fetched": 0,
}
SET_MAX_INDEX = 120
_PRICE_DETAIL_CACHE_TTL_SECONDS = 60 * 30
_PRICE_DETAIL_CACHE: dict[str, tuple[float, dict[str, Optional[float]]]] = {}

_CARD_ANCHOR_PATTERN = re.compile(
    r'href="(?P<href>/game/pokemon-japanese-nihil-zero/[^"]+)">\s*(?P<name>[^<]+?)\s*</a>',
    re.IGNORECASE,
)
_NUM_IN_NAME_PATTERN = re.compile(r"#\s*(\d{1,3})")
_NUM_IN_HREF_PATTERN = re.compile(r"-(\d{1,3})(?:$|[/?#])")
_PRICE_PATTERN = re.compile(r"\$(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)")
_UNGD_PATTERN = re.compile(r"Ungraded[^$]{0,120}\$(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)", re.IGNORECASE | re.DOTALL)
_GRADE9_PATTERN = re.compile(r"Grade\s*9[^$]{0,140}\$(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)", re.IGNORECASE | re.DOTALL)
_PSA10_PATTERN = re.compile(r"PSA\s*10[^$]{0,140}\$(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)", re.IGNORECASE | re.DOTALL)


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


def _parse_price(text: str) -> Optional[float]:
    match = _PRICE_PATTERN.search(text)
    if not match:
        return None
    return float(match.group(1).replace(",", ""))


def _card_id_from_href(href: str) -> str:
    slug = href.rsplit("/", 1)[-1]
    return f"nihil-zero-{slug}"


def _fetch_html(url: str) -> str:
    request_headers = {
        "User-Agent": "Mozilla/5.0 (compatible; poke-scan/0.1; +http://localhost)",
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        request = Request(url=url, headers=request_headers)
        with urlopen(request, timeout=10) as response:
            return response.read().decode("utf-8", errors="ignore")
    except (URLError, HTTPError, TimeoutError):
        cmd = [
            "curl",
            "-L",
            "--fail",
            "--silent",
            "--show-error",
            "--http1.1",
            "--retry",
            "2",
            "--retry-delay",
            "1",
            "--max-time",
            "15",
            "-A",
            request_headers["User-Agent"],
            "-H",
            f"Accept-Language: {request_headers['Accept-Language']}",
            url,
        ]
        try:
            out = subprocess.run(cmd, check=True, capture_output=True, text=True)
            return out.stdout
        except (subprocess.SubprocessError, FileNotFoundError) as exc:
            if "pricecharting.com" in url:
                mirror = url.replace("https://", "https://r.jina.ai/http://", 1)
                mirror_cmd = [
                    "curl",
                    "-L",
                    "--fail",
                    "--silent",
                    "--show-error",
                    "--http1.1",
                    "--max-time",
                    "20",
                    "-A",
                    request_headers["User-Agent"],
                    mirror,
                ]
                try:
                    mirrored = subprocess.run(mirror_cmd, check=True, capture_output=True, text=True)
                    return mirrored.stdout
                except (subprocess.SubprocessError, FileNotFoundError) as mirror_exc:
                    raise URLError(f"{exc}; mirror_failed={mirror_exc}") from mirror_exc
            raise URLError(str(exc)) from exc


def _parse_number_index(name: str, href: str) -> Optional[str]:
    name_match = _NUM_IN_NAME_PATTERN.search(name)
    if name_match:
        return str(int(name_match.group(1)))

    href_match = _NUM_IN_HREF_PATTERN.search(href)
    if href_match:
        return str(int(href_match.group(1)))

    return None


def _parse_cards(html: str) -> list[CatalogCard]:
    cards: list[CatalogCard] = []

    for match in _CARD_ANCHOR_PATTERN.finditer(html):
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
                card_id=_card_id_from_href(href),
                name=clean_name or label,
                set_name="Pokemon Japanese Nihil Zero",
                number_index=number_index,
                number=number_index,
                market_price_usd=price,
                grade_9_price_usd=None,
                psa_10_price_usd=None,
                source_url=full_url,
            )
        )

    mirror_pattern = re.compile(
        r"\[(?P<name>[^\]]+?)\s*#(?P<number>\d{1,3})\]\((?P<url>https://www\.pricecharting\.com/game/pokemon-japanese-nihil-zero/[^\)]+)\)",
        re.IGNORECASE,
    )
    for match in mirror_pattern.finditer(html):
        label = re.sub(r"\s+", " ", match.group("name")).strip()
        number_index = str(int(match.group("number")))
        full_url = match.group("url")
        href = "/" + full_url.split("pricecharting.com/", 1)[1]
        snippet = html[match.end() : match.end() + 220]
        price = _parse_price(snippet)

        cards.append(
            CatalogCard(
                card_id=_card_id_from_href(href),
                name=label,
                set_name="Pokemon Japanese Nihil Zero",
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


def _fetch_set_pages() -> tuple[list[CatalogCard], int]:
    all_cards: dict[str, CatalogCard] = {}
    pages_fetched = 0

    # PriceCharting may paginate set listing. Try a bounded number of pages.
    for page in range(1, 8):
        url = SET_URL if page == 1 else f"{SET_URL}?page={page}"
        html = _fetch_html(url)
        page_cards = _parse_cards(html)
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


def _persist_cards(cards: list[CatalogCard]) -> None:
    _PERSIST_PATH.parent.mkdir(parents=True, exist_ok=True)
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
    _PERSIST_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_persisted_cards() -> list[CatalogCard]:
    if not _PERSIST_PATH.exists():
        return []

    try:
        payload = json.loads(_PERSIST_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    cards: list[CatalogCard] = []
    for item in payload:
        cards.append(
            CatalogCard(
                card_id=str(item.get("card_id", "")),
                name=str(item.get("name", "")),
                set_name=str(item.get("set_name", "Pokemon Japanese Nihil Zero")),
                number_index=str(item.get("number_index", "")),
                number=item.get("number"),
                market_price_usd=item.get("market_price_usd"),
                grade_9_price_usd=item.get("grade_9_price_usd"),
                psa_10_price_usd=item.get("psa_10_price_usd"),
                source_url=str(item.get("source_url", "")),
            )
        )
    return [card for card in cards if card.card_id and card.name and card.number_index]


def load_nihil_zero_catalog(force_refresh: bool = False) -> list[CatalogCard]:
    now = time.time()
    cached_at = float(_CACHE["fetched_at"])
    cached_cards = _CACHE["cards"]

    if not force_refresh and cached_cards and now - cached_at < _CACHE_TTL_SECONDS:
        return list(cached_cards)  # type: ignore[return-value]

    try:
        parsed_cards, pages_fetched = _fetch_set_pages()
        if parsed_cards:
            _CACHE["fetched_at"] = now
            _CACHE["cards"] = parsed_cards
            _persist_cards(parsed_cards)
            _META["last_error"] = None
            _META["last_error_detail"] = None
            _META["last_sync_source"] = "live_set_page"
            _META["pages_fetched"] = pages_fetched
            return parsed_cards
    except (URLError, HTTPError, TimeoutError) as exc:
        _META["last_error"] = "network_error_set_page"
        _META["last_error_detail"] = str(exc)

    persisted = _load_persisted_cards()
    if persisted:
        _CACHE["fetched_at"] = now
        _CACHE["cards"] = persisted
        _META["last_sync_source"] = "local_cache_file"
        return persisted

    return list(cached_cards) if cached_cards else []  # type: ignore[return-value]


def get_catalog_meta() -> dict[str, object]:
    return {
        "cards_loaded": len(_CACHE.get("cards", [])),
        "fetched_at": _CACHE.get("fetched_at"),
        "last_error": _META.get("last_error"),
        "last_error_detail": _META.get("last_error_detail"),
        "last_sync_source": _META.get("last_sync_source"),
        "pages_fetched": _META.get("pages_fetched"),
        "cache_file": str(_PERSIST_PATH),
    }


def _parse_card_link_from_search(html: str, number_index: str) -> Optional[tuple[str, str]]:
    # Preferred match: explicit "#<number>" in anchor text.
    pattern = re.compile(
        rf'href="(?P<href>/game/pokemon-japanese-nihil-zero/[^"]+)">(?P<name>[^<]*?)\s*#0*{re.escape(number_index)}\s*</a>',
        re.IGNORECASE,
    )
    match = pattern.search(html)
    if match:
        href = match.group("href")
        name = re.sub(r"\s+", " ", match.group("name")).strip()
        return href, name

    # Fallback: any Nihil Zero card link whose slug/name infers the same number.
    generic = re.compile(
        r'href="(?P<href>/game/pokemon-japanese-nihil-zero/[^"]+)">(?P<name>[^<]*?)</a>',
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


def _parse_ungraded_from_card_page(html: str) -> Optional[float]:
    match = _UNGD_PATTERN.search(html)
    if not match:
        return None
    return float(match.group(1).replace(",", ""))


def _parse_grade9_from_card_page(html: str) -> Optional[float]:
    match = _GRADE9_PATTERN.search(html)
    if not match:
        return None
    return float(match.group(1).replace(",", ""))


def _parse_psa10_from_card_page(html: str) -> Optional[float]:
    match = _PSA10_PATTERN.search(html)
    if not match:
        return None
    return float(match.group(1).replace(",", ""))


def fetch_card_price_details(source_url: str) -> dict[str, Optional[float]]:
    now = time.time()
    cached = _PRICE_DETAIL_CACHE.get(source_url)
    if cached and now - cached[0] < _PRICE_DETAIL_CACHE_TTL_SECONDS:
        return cached[1]

    try:
        html = _fetch_html(source_url)
    except (URLError, HTTPError, TimeoutError):
        details = {"ungraded": None, "grade_9": None, "psa_10": None}
        _PRICE_DETAIL_CACHE[source_url] = (now, details)
        return details

    details = {
        "ungraded": _parse_ungraded_from_card_page(html),
        "grade_9": _parse_grade9_from_card_page(html),
        "psa_10": _parse_psa10_from_card_page(html),
    }
    _PRICE_DETAIL_CACHE[source_url] = (now, details)
    return details


def find_card_by_number_online(number_index: str) -> Optional[CatalogCard]:
    number_plain = str(int(number_index))
    number_padded = number_plain.zfill(3)
    queries = [
        f"pokemon japanese nihil zero #{number_plain}",
        f"pokemon japanese nihil zero #{number_padded}",
        f"pokemon japanese nihil zero {number_plain}",
        f"pokemon japanese nihil zero {number_padded}",
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
        card_link = _parse_card_link_from_search(search_html, number_plain)
        if card_link:
            break

    if not card_link and last_exc:
        _META["last_error"] = "network_error_search_page"
        _META["last_error_detail"] = str(last_exc)
        return None

    if not card_link:
        _META["last_error"] = "search_no_matching_card_link"
        _META["last_error_detail"] = f"number_index={number_plain}"
        return None

    href, name = card_link
    full_url = f"https://www.pricecharting.com{href}"

    try:
        card_html = _fetch_html(full_url)
    except (URLError, HTTPError, TimeoutError) as exc:
        _META["last_error"] = "network_error_card_page"
        _META["last_error_detail"] = str(exc)
        return None

    ungraded = _parse_ungraded_from_card_page(card_html)
    grade_9 = _parse_grade9_from_card_page(card_html)
    psa_10 = _parse_psa10_from_card_page(card_html)
    _PRICE_DETAIL_CACHE[full_url] = (
        time.time(),
        {"ungraded": ungraded, "grade_9": grade_9, "psa_10": psa_10},
    )
    _META["last_error"] = None
    _META["last_error_detail"] = None
    _META["last_sync_source"] = "online_number_fallback"
    return CatalogCard(
        card_id=_card_id_from_href(href),
        name=name,
        set_name="Pokemon Japanese Nihil Zero",
        number_index=number_plain,
        number=number_plain,
        market_price_usd=ungraded,
        grade_9_price_usd=grade_9,
        psa_10_price_usd=psa_10,
        source_url=full_url,
    )


def rebuild_catalog_from_number_search(start: int = 1, end: int = SET_MAX_INDEX) -> list[CatalogCard]:
    existing = {card.card_id: card for card in load_nihil_zero_catalog(force_refresh=True)}
    rebuilt: dict[str, CatalogCard] = dict(existing)

    for number in range(start, end + 1):
        online = find_card_by_number_online(str(number))
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
        _CACHE["fetched_at"] = now
        _CACHE["cards"] = cards
        _persist_cards(cards)
        _META["last_error"] = None
        _META["last_error_detail"] = None
        _META["last_sync_source"] = "number_search_rebuild"
    return cards
