import io
import re
from typing import Optional

from PIL import Image, ImageEnhance, ImageFilter, ImageOps
import pytesseract
from pytesseract import TesseractError

NUMBER_REGEX = re.compile(r"(?<!\d)(\d{1,3})\s*[\/|\\]\s*(\d{1,3})(?!\d)")
NAME_LINE_REGEX = re.compile(r"^[A-Za-z][A-Za-z'\- ]{1,30}$")
ALPHA_HEAVY_LINE_REGEX = re.compile(r"^[A-Za-z][A-Za-z0-9'\- ]{1,30}$")
JP_LINE_REGEX = re.compile(r"^[\u3040-\u30FF\u4E00-\u9FFFー・\s]{2,24}$")
NUMBER_NORMALIZATION_MAP = str.maketrans(
    {
        "O": "0",
        "o": "0",
        "I": "1",
        "l": "1",
        "S": "5",
        "s": "5",
        "B": "8",
        "Z": "2",
    }
)
TARGET_SET_DENOMINATOR = "080"
MAX_VALID_COLLECTOR_INDEX = 200


class OCRResult:
    def __init__(self, raw_text: str, extracted_number: Optional[str], extracted_name: Optional[str]):
        self.raw_text = raw_text
        self.extracted_number = extracted_number
        self.extracted_name = extracted_name


def _build_ocr_variants(image: Image.Image) -> list[tuple[str, Image.Image]]:
    base = image.convert("RGB")
    gray = ImageOps.grayscale(base)
    high_contrast = ImageEnhance.Contrast(gray).enhance(2.2)
    sharpened = high_contrast.filter(ImageFilter.UnsharpMask(radius=1.2, percent=190, threshold=2))
    binary = sharpened.point(lambda px: 255 if px > 145 else 0, mode="1").convert("L")
    upscaled = sharpened.resize((sharpened.width * 2, sharpened.height * 2), Image.Resampling.LANCZOS)
    upscaled_binary = binary.resize((binary.width * 2, binary.height * 2), Image.Resampling.NEAREST)

    return [
        ("base", base),
        ("sharpened", sharpened),
        ("binary", binary),
        ("upscaled", upscaled),
        ("upscaled_binary", upscaled_binary),
    ]


def _collect_ocr_text(variants: list[tuple[str, Image.Image]]) -> tuple[str, list[str]]:
    configs = ("--oem 3 --psm 6", "--oem 3 --psm 11")
    snippets: list[str] = []
    langs = _available_ocr_langs()
    use_japanese = "jpn" in langs
    lang_arg = "eng+jpn" if use_japanese else "eng"

    for variant_name, variant_image in variants:
        for config in configs:
            try:
                text = pytesseract.image_to_string(variant_image, lang=lang_arg, config=config).strip()
            except TesseractError:
                text = pytesseract.image_to_string(variant_image, lang="eng", config=config).strip()
            if not text:
                continue
            snippets.append(f"[{variant_name} | {config}]")
            snippets.append(text)

    combined_text = "\n".join(snippets).strip()
    return combined_text, snippets


def _available_ocr_langs() -> set[str]:
    try:
        langs = pytesseract.get_languages(config="")
    except TesseractError:
        return {"eng"}
    return set(langs)


def _extract_collector_numbers_from_text(text: str) -> list[str]:
    results: list[str] = []
    for match in NUMBER_REGEX.finditer(text):
        results.append(f"{match.group(1)}/{match.group(2)}")
    return results


def _extract_collector_number(raw_text: str) -> Optional[str]:
    direct = _extract_collector_numbers_from_text(raw_text)
    normalized = raw_text.translate(NUMBER_NORMALIZATION_MAP)
    normalized_match = _extract_collector_numbers_from_text(normalized)

    matches = list(dict.fromkeys([*direct, *normalized_match]))
    if not matches:
        return None

    def score(candidate: str) -> tuple[int, int, int]:
        left, right = candidate.split("/")
        try:
            left_value = int(left)
            right_value = int(right)
        except ValueError:
            return (0, 0, 0)

        denom_target = 2 if right_value == int(TARGET_SET_DENOMINATOR) else 0
        valid_index = 1 if 1 <= left_value <= MAX_VALID_COLLECTOR_INDEX else 0
        digit_quality = len(left) + len(right)
        return (denom_target, valid_index, digit_quality)

    best = max(matches, key=score)
    left, right = best.split("/")
    try:
        left_value = int(left)
        right_value = int(right)
    except ValueError:
        return None

    if right_value == int(TARGET_SET_DENOMINATOR) and not (1 <= left_value <= MAX_VALID_COLLECTOR_INDEX):
        return None

    return f"{left_value}/{str(right_value).zfill(3)}"


def _line_quality_score(line: str) -> float:
    alpha_count = sum(char.isalpha() for char in line)
    ratio = alpha_count / max(len(line), 1)
    tokens = line.split()
    token_penalty = 0.0 if 1 <= len(tokens) <= 4 else 0.15
    return ratio - token_penalty


def _extract_name_guess(raw_text: str) -> Optional[str]:
    lines = [line.strip() for line in raw_text.splitlines() if line.strip() and not line.startswith("[")]
    candidates: list[str] = []

    for line in lines[:14]:
        cleaned = re.sub(r"\s+", " ", line)
        if JP_LINE_REGEX.match(cleaned):
            candidates.append(cleaned)
            continue

        if NAME_LINE_REGEX.match(cleaned):
            candidates.append(cleaned)
            continue

        if ALPHA_HEAVY_LINE_REGEX.match(cleaned):
            if any(char.isdigit() for char in cleaned):
                continue
            candidates.append(cleaned)

    if not candidates:
        return None

    best = max(candidates, key=_line_quality_score)
    if len(best) > 30:
        return best[:30].rstrip()
    return best


def run_ocr(image_bytes: bytes) -> OCRResult:
    image = Image.open(io.BytesIO(image_bytes))
    variants = _build_ocr_variants(image)
    raw_text, snippets = _collect_ocr_text(variants)

    if not snippets:
        raw_text = ""

    extracted_number = _extract_collector_number(raw_text)
    extracted_name = _extract_name_guess(raw_text)

    # Backup name extraction from normalized text when OCR is noisy.
    if not extracted_name:
        extracted_name = _extract_name_guess(raw_text.translate(NUMBER_NORMALIZATION_MAP))

    return OCRResult(
        raw_text=raw_text,
        extracted_number=extracted_number,
        extracted_name=extracted_name,
    )


def run_ocr_single_pass(image_bytes: bytes) -> OCRResult:
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    raw_text = pytesseract.image_to_string(
        image,
        lang="eng",
        config="--oem 3 --psm 6",
    )
    extracted_number = _extract_collector_number(raw_text)
    extracted_name = _extract_name_guess(raw_text)
    return OCRResult(
        raw_text=raw_text.strip(),
        extracted_number=extracted_number,
        extracted_name=extracted_name,
    )
