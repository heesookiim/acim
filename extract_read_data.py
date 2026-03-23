#!/usr/bin/env python3
"""Build reader-oriented side-by-side units from textbook page images.

This extractor is separate from extract_text.py and is intentionally scoped to
the initial Chapter 1 slice agreed during planning:

- Park PDF pages 13-27
- Original PDF pages 15-29

The extractor renders PDF pages to PNG images, sends those images to Gemini as
multimodal inputs, and writes read_data.json using Park as the canonical unit
structure for ordering and numbering.

Environment:
  GEMINI_API_KEY=<your key>

Optional environment:
  GEMINI_MODEL=gemini-2.5-flash

Examples:
  python3.11 extract_read_data.py
  python3.11 extract_read_data.py --render-only
  python3.11 extract_read_data.py --force-refresh --output read_data.chapter1.json
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_PATH = BASE_DIR / "read_data.json"
DEFAULT_CACHE_DIR = BASE_DIR / ".cache" / "read_data"
PARK_PDF_PATH = BASE_DIR / "version" / "park" / "교과서_박영수.pdf"
ORIGINAL_PDF_PATH = BASE_DIR / "version" / "original" / "교과서_원본.pdf"
DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
API_KEY_ENV = "GEMINI_API_KEY"
REVIEW_THRESHOLD = 0.5

INITIAL_SCOPE = {
    "chapter": {
        "chapter_id": "ch01",
        "chapter_number": 1,
        "title_by_version": {
            "park": "제1장 기적에 대한 소개",
            "original": "제1장 기적에 대한 안내",
        },
    },
    "sections": [
        {
            "section_id": "ch01-sec01",
            "section_number": 1,
            "park_page_start": 13,
            "park_page_end": 24,
            "title_by_version": {
                "park": "기적의 원리",
                "original": "I. 기적의 원리",
            },
        },
        {
            "section_id": "ch01-sec02",
            "section_number": 2,
            "park_page_start": 25,
            "park_page_end": 27,
            "title_by_version": {
                "park": "기적 충동의 왜곡",
                "original": "II. 기적 충동의 왜곡",
            },
        },
    ],
    "park_pdf_page_start": 13,
    "park_pdf_page_end": 27,
    "original_pdf_page_start": 15,
    "original_pdf_page_end": 29,
}


class ExtractorError(RuntimeError):
    pass


class GeminiClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        cache_dir: Path,
        force_refresh: bool,
        max_retries: int,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.cache_dir = cache_dir
        self.force_refresh = force_refresh
        self.max_retries = max_retries
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def generate_json(
        self,
        *,
        system_instruction: str,
        parts: list[dict[str, Any]],
        cache_key: str,
        temperature: float = 0.1,
    ) -> dict[str, Any]:
        cache_path = self.cache_dir / f"{cache_key}.json"
        if cache_path.exists() and not self.force_refresh:
            return json.loads(cache_path.read_text(encoding="utf-8"))

        payload = {
            "systemInstruction": {
                "parts": [{"text": system_instruction}],
            },
            "contents": [
                {
                    "role": "user",
                    "parts": parts,
                }
            ],
            "generationConfig": {
                "temperature": temperature,
                "responseMimeType": "application/json",
            },
        }
        response = self._post_json(payload)
        parsed = self._extract_response_json(response)
        cache_path.write_text(
            json.dumps(parsed, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return parsed

    def _post_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        encoded_model = urllib.parse.quote(self.model, safe="")
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{encoded_model}:generateContent?key={self.api_key}"
        )
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        delay_seconds = 2.0
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=240) as response:
                    body = response.read().decode("utf-8")
                    return json.loads(body)
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                transient = exc.code in {408, 429, 500, 502, 503, 504}
                last_error = ExtractorError(f"Gemini HTTP {exc.code}: {body[:1500]}")
                if not transient or attempt == self.max_retries:
                    break
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt == self.max_retries:
                    break
            time.sleep(delay_seconds)
            delay_seconds *= 2

        raise ExtractorError(f"Gemini request failed: {last_error}")

    @staticmethod
    def _extract_response_json(response: dict[str, Any]) -> dict[str, Any]:
        candidates = response.get("candidates") or []
        if not candidates:
            raise ExtractorError(f"Gemini response contained no candidates: {response}")

        parts = candidates[0].get("content", {}).get("parts", [])
        raw_text = "\n".join(part.get("text", "") for part in parts if part.get("text")).strip()
        if not raw_text:
            raise ExtractorError(f"Gemini response did not include JSON text: {response}")

        candidates_to_try: list[str] = [raw_text]
        extracted_block = GeminiClient._extract_balanced_json_block(raw_text)
        if extracted_block and extracted_block != raw_text:
            candidates_to_try.append(extracted_block)

        for candidate_text in list(candidates_to_try):
            sanitized = GeminiClient._escape_control_chars_in_json_strings(candidate_text)
            if sanitized != candidate_text:
                candidates_to_try.append(sanitized)

        for candidate_text in candidates_to_try:
            try:
                parsed = json.loads(candidate_text)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed

        raise ExtractorError(f"Gemini returned invalid JSON: {raw_text[:1500]}")

    @staticmethod
    def _extract_balanced_json_block(text: str) -> str | None:
        start_index = None
        stack: list[str] = []
        in_string = False
        escaping = False

        for index, char in enumerate(text):
            if start_index is None:
                if char in "[{":
                    start_index = index
                    stack.append("}" if char == "{" else "]")
                continue

            if in_string:
                if escaping:
                    escaping = False
                elif char == "\\":
                    escaping = True
                elif char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
                continue

            if char in "[{":
                stack.append("}" if char == "{" else "]")
                continue

            if char in "]}":
                if not stack or char != stack[-1]:
                    return None
                stack.pop()
                if not stack:
                    return text[start_index : index + 1]

        return None

    @staticmethod
    def _escape_control_chars_in_json_strings(text: str) -> str:
        chunks: list[str] = []
        in_string = False
        escaping = False

        for char in text:
            if in_string:
                if escaping:
                    chunks.append(char)
                    escaping = False
                    continue

                if char == "\\":
                    chunks.append(char)
                    escaping = True
                    continue

                if char == '"':
                    chunks.append(char)
                    in_string = False
                    continue

                if ord(char) < 0x20:
                    replacements = {
                        "\b": "\\b",
                        "\f": "\\f",
                        "\n": "\\n",
                        "\r": "\\r",
                        "\t": "\\t",
                    }
                    chunks.append(replacements.get(char, f"\\u{ord(char):04x}"))
                    continue

                chunks.append(char)
                continue

            chunks.append(char)
            if char == '"':
                in_string = True

        return "".join(chunks)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract reader-oriented side-by-side units from textbook page images."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help="Output JSON path (default: read_data.json)",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=DEFAULT_CACHE_DIR,
        help="Cache directory for rendered images and Gemini responses",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="Gemini multimodal model name",
    )
    parser.add_argument(
        "--zoom",
        type=float,
        default=2.0,
        help="PDF render zoom factor for page images",
    )
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Ignore cached images and Gemini responses",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Gemini retry count",
    )
    parser.add_argument(
        "--render-only",
        action="store_true",
        help="Render and cache page images without making Gemini requests",
    )
    return parser.parse_args()


def import_fitz():
    try:
        import fitz  # type: ignore
    except ModuleNotFoundError as exc:
        raise ExtractorError(
            "PyMuPDF is required in the runtime interpreter. Use the same interpreter that already runs extract_text.py successfully."
        ) from exc
    return fitz


def make_cache_key(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def render_pdf_pages(
    pdf_path: Path,
    *,
    pdf_page_start: int,
    pdf_page_end: int,
    cache_dir: Path,
    zoom: float,
    force_refresh: bool,
) -> list[dict[str, Any]]:
    fitz = import_fitz()
    image_dir = cache_dir / "page_images" / pdf_path.stem
    image_dir.mkdir(parents=True, exist_ok=True)

    pages: list[dict[str, Any]] = []
    with fitz.open(pdf_path) as document:
        for pdf_page in range(pdf_page_start, pdf_page_end + 1):
            page = document[pdf_page - 1]
            image_path = image_dir / f"page_{pdf_page:04d}.png"
            if force_refresh or not image_path.exists():
                matrix = fitz.Matrix(zoom, zoom)
                pixmap = page.get_pixmap(matrix=matrix, alpha=False)
                image_path.write_bytes(pixmap.tobytes("png"))
            pages.append(
                {
                    "pdf_page": pdf_page,
                    "image_path": image_path,
                }
            )
    return pages


def image_part(image_path: Path) -> dict[str, Any]:
    encoded = base64.b64encode(image_path.read_bytes()).decode("utf-8")
    return {
        "inlineData": {
            "mimeType": "image/png",
            "data": encoded,
        }
    }


def build_park_extraction_parts(rendered_pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scope = INITIAL_SCOPE
    prompt = {
        "task": "Extract canonical Park reading units from page images.",
        "rules": [
            "Park is the source of truth for chapter, section, and unit boundaries.",
            "A new unit begins only when a numbered paragraph visibly starts at the front of a paragraph.",
            "Any following sub-paragraph without a leading number belongs to the previous numbered unit and must be appended to that unit.",
            "Numbering resets at each Park section start.",
            "Capture printed page labels from the rendered page images when visible.",
            "Extract footnotes if present on the Park side, otherwise return an empty array.",
            "Return strict JSON only.",
        ],
        "chapter": scope["chapter"],
        "sections": scope["sections"],
        "required_response_shape": {
            "units": [
                {
                    "unit_id": "ch01-sec01-u001",
                    "section_id": "ch01-sec01",
                    "section_number": 1,
                    "unit_number": 1,
                    "displayed_number": 1,
                    "text": "...",
                    "footnotes": [
                        {
                            "text": "...",
                            "marker": "1)",
                            "confidence": 0.0,
                            "printed_page_label": "14",
                            "pdf_page": 14,
                        }
                    ],
                    "confidence": 0.0,
                    "pdf_page_start": 14,
                    "pdf_page_end": 15,
                    "printed_page_label_start": "14",
                    "printed_page_label_end": "15",
                    "extraction_notes": None,
                }
            ]
        },
        "pages": [page["pdf_page"] for page in rendered_pages],
    }
    parts: list[dict[str, Any]] = [
        {
            "text": json.dumps(prompt, ensure_ascii=False, indent=2),
        }
    ]
    for page in rendered_pages:
        parts.append({"text": f"Park PDF page {page['pdf_page']}"})
        parts.append(image_part(page["image_path"]))
    return parts


def build_original_alignment_parts(
    rendered_pages: list[dict[str, Any]],
    park_units: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    prompt = {
        "task": "Align original textbook content from page images to the provided canonical Park units.",
        "rules": [
            "Do not change the Park unit order, chapter, section, or numbering.",
            "Original content may be messy; merge multiple original fragments if they belong to one Park unit.",
            "If a match is unclear, return status ambiguous and a lower confidence score.",
            "If no acceptable match exists, return status missing and keep text empty.",
            "Capture printed page labels from original page images when visible.",
            "Return strict JSON only.",
        ],
        "chapter": INITIAL_SCOPE["chapter"],
        "sections": INITIAL_SCOPE["sections"],
        "canonical_park_units": [
            {
                "unit_id": unit["unit_id"],
                "section_id": unit["section"]["section_id"],
                "section_number": unit["section"]["section_number"],
                "displayed_number": unit["displayed_number"],
                "park_text": unit["versions"]["park"]["text"],
                "park_pdf_page_start": unit["versions"]["park"]["pdf_page_start"],
                "park_pdf_page_end": unit["versions"]["park"]["pdf_page_end"],
            }
            for unit in park_units
        ],
        "required_response_shape": {
            "units": [
                {
                    "unit_id": "ch01-sec01-u001",
                    "text": "...",
                    "footnotes": [
                        {
                            "text": "...",
                            "marker": "1)",
                            "confidence": 0.0,
                            "printed_page_label": "16",
                            "pdf_page": 16,
                        }
                    ],
                    "confidence": 0.0,
                    "pdf_page_start": 16,
                    "pdf_page_end": 17,
                    "printed_page_label_start": "16",
                    "printed_page_label_end": "17",
                    "extraction_notes": "merged from multiple original fragments",
                    "alignment_status": "merged",
                    "alignment_confidence": 0.0,
                    "review_reason": None,
                    "original_fragment_ids": ["orig-p16-f01", "orig-p17-f01"],
                }
            ]
        },
        "pages": [page["pdf_page"] for page in rendered_pages],
    }
    parts: list[dict[str, Any]] = [
        {
            "text": json.dumps(prompt, ensure_ascii=False, indent=2),
        }
    ]
    for page in rendered_pages:
        parts.append({"text": f"Original PDF page {page['pdf_page']}"})
        parts.append(image_part(page["image_path"]))
    return parts


def extract_park_units(client: GeminiClient, rendered_pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    system_instruction = (
        "You are extracting canonical reading units from Korean textbook page images. "
        "Use only what is visible in the provided page images. Return strict JSON."
    )
    parts = build_park_extraction_parts(rendered_pages)
    response = client.generate_json(
        system_instruction=system_instruction,
        parts=parts,
        cache_key=make_cache_key("park-units", client.model, json.dumps(INITIAL_SCOPE, ensure_ascii=False)),
    )

    park_units: list[dict[str, Any]] = []
    section_lookup = {section["section_id"]: section for section in INITIAL_SCOPE["sections"]}
    chapter_meta = INITIAL_SCOPE["chapter"]
    for raw_unit in response.get("units", []):
        unit_id = str(raw_unit.get("unit_id", "")).strip()
        section_id = str(raw_unit.get("section_id", "")).strip()
        if not unit_id or section_id not in section_lookup:
            continue
        section_meta = section_lookup[section_id]
        unit_number = int(raw_unit.get("unit_number", raw_unit.get("displayed_number", 0)) or 0)
        if unit_number <= 0:
            continue
        park_units.append(
            {
                "unit_id": unit_id,
                "sort_key": {
                    "chapter_number": chapter_meta["chapter_number"],
                    "section_number": section_meta["section_number"],
                    "unit_number": unit_number,
                },
                "chapter": {
                    "chapter_id": chapter_meta["chapter_id"],
                    "chapter_number": chapter_meta["chapter_number"],
                    "title_by_version": chapter_meta["title_by_version"],
                },
                "section": {
                    "section_id": section_meta["section_id"],
                    "section_number": section_meta["section_number"],
                    "title_by_version": section_meta["title_by_version"],
                },
                "displayed_number": int(raw_unit.get("displayed_number", unit_number) or unit_number),
                "versions": {
                    "park": {
                        "text": normalize_text(raw_unit.get("text", "")),
                        "footnotes": normalize_footnotes(raw_unit.get("footnotes", [])),
                        "confidence": clamp_confidence(raw_unit.get("confidence")),
                        "pdf_page_start": int(raw_unit.get("pdf_page_start", 0) or 0),
                        "pdf_page_end": int(raw_unit.get("pdf_page_end", 0) or 0),
                        "printed_page_label_start": normalize_page_label(raw_unit.get("printed_page_label_start")),
                        "printed_page_label_end": normalize_page_label(raw_unit.get("printed_page_label_end")),
                        "extraction_notes": nullable_text(raw_unit.get("extraction_notes")),
                    },
                    "original": {
                        "text": "",
                        "footnotes": [],
                        "confidence": None,
                        "pdf_page_start": None,
                        "pdf_page_end": None,
                        "printed_page_label_start": None,
                        "printed_page_label_end": None,
                        "extraction_notes": None,
                    },
                },
                "alignment": {
                    "status": "missing",
                    "confidence": 0.0,
                    "needs_manual_review": True,
                    "review_reason": "original side not aligned yet",
                    "review_notes": None,
                    "reviewed_by": None,
                    "last_reviewed_at": None,
                    "source_of_truth": "park",
                    "original_fragment_ids": [],
                },
            }
        )

    park_units.sort(
        key=lambda item: (
            item["sort_key"]["chapter_number"],
            item["sort_key"]["section_number"],
            item["sort_key"]["unit_number"],
        )
    )
    return park_units


def align_original_to_park(
    client: GeminiClient,
    rendered_pages: list[dict[str, Any]],
    park_units: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    system_instruction = (
        "You are aligning content from a messy Korean textbook version to canonical Park reading units using page images. "
        "Use only the supplied original page images and the provided Park units. Return strict JSON."
    )
    parts = build_original_alignment_parts(rendered_pages, park_units)
    response = client.generate_json(
        system_instruction=system_instruction,
        parts=parts,
        cache_key=make_cache_key(
            "original-align",
            client.model,
            json.dumps([unit["unit_id"] for unit in park_units], ensure_ascii=False),
        ),
    )

    aligned_units: dict[str, dict[str, Any]] = {}
    for raw_unit in response.get("units", []):
        unit_id = str(raw_unit.get("unit_id", "")).strip()
        if not unit_id:
            continue
        alignment_confidence = clamp_confidence(raw_unit.get("alignment_confidence")) or 0.0
        alignment_status = str(raw_unit.get("alignment_status", "missing")).strip() or "missing"
        needs_manual_review = alignment_confidence < REVIEW_THRESHOLD or alignment_status in {"ambiguous", "missing"}
        aligned_units[unit_id] = {
            "version": {
                "text": normalize_text(raw_unit.get("text", "")),
                "footnotes": normalize_footnotes(raw_unit.get("footnotes", [])),
                "confidence": clamp_confidence(raw_unit.get("confidence")),
                "pdf_page_start": coerce_optional_int(raw_unit.get("pdf_page_start")),
                "pdf_page_end": coerce_optional_int(raw_unit.get("pdf_page_end")),
                "printed_page_label_start": normalize_page_label(raw_unit.get("printed_page_label_start")),
                "printed_page_label_end": normalize_page_label(raw_unit.get("printed_page_label_end")),
                "extraction_notes": nullable_text(raw_unit.get("extraction_notes")),
            },
            "alignment": {
                "status": alignment_status,
                "confidence": alignment_confidence,
                "needs_manual_review": needs_manual_review,
                "review_reason": nullable_text(raw_unit.get("review_reason")) or (
                    "confidence below threshold" if needs_manual_review else None
                ),
                "review_notes": None,
                "reviewed_by": None,
                "last_reviewed_at": None,
                "source_of_truth": "park",
                "original_fragment_ids": normalize_fragment_ids(raw_unit.get("original_fragment_ids", [])),
            },
        }
    return aligned_units


def normalize_footnotes(raw_footnotes: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_footnotes, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in raw_footnotes:
        if isinstance(item, str):
            text = normalize_text(item)
            if not text:
                continue
            normalized.append(
                {
                    "text": text,
                    "marker": None,
                    "confidence": None,
                    "printed_page_label": None,
                    "pdf_page": None,
                }
            )
            continue
        if not isinstance(item, dict):
            continue
        text = normalize_text(item.get("text", ""))
        if not text:
            continue
        normalized.append(
            {
                "text": text,
                "marker": nullable_text(item.get("marker")),
                "confidence": clamp_confidence(item.get("confidence")),
                "printed_page_label": normalize_page_label(item.get("printed_page_label")),
                "pdf_page": coerce_optional_int(item.get("pdf_page")),
            }
        )
    return normalized


def normalize_fragment_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def normalize_page_label(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalize_text(value: Any) -> str:
    text = re.sub(r"[ \t\xa0]+", " ", str(value or ""))
    text = re.sub(r"\s*\n\s*", " ", text)
    return text.strip()


def nullable_text(value: Any) -> str | None:
    text = normalize_text(value)
    return text or None


def clamp_confidence(value: Any) -> float | None:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, confidence))


def coerce_optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def attach_original_versions(
    park_units: list[dict[str, Any]],
    original_by_unit: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    merged_units: list[dict[str, Any]] = []
    for unit in park_units:
        original_data = original_by_unit.get(unit["unit_id"])
        if original_data is not None:
            unit["versions"]["original"] = original_data["version"]
            unit["alignment"] = original_data["alignment"]
        merged_units.append(unit)
    return merged_units


def build_output(units: list[dict[str, Any]], model: str) -> dict[str, Any]:
    chapter_meta = INITIAL_SCOPE["chapter"]
    sections_meta = INITIAL_SCOPE["sections"]
    units_by_section: dict[str, list[dict[str, Any]]] = {section["section_id"]: [] for section in sections_meta}
    for unit in units:
        units_by_section[unit["section"]["section_id"]].append(unit)

    sections = []
    for section in sections_meta:
        sections.append(
            {
                "section_id": section["section_id"],
                "section_number": section["section_number"],
                "title_by_version": section["title_by_version"],
                "units": units_by_section[section["section_id"]],
            }
        )

    return {
        "metadata": {
            "schema_version": "1.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "canonical_version": "park",
            "confidence_threshold_for_review": REVIEW_THRESHOLD,
            "extraction_method": "gemini_multimodal_page_images",
            "display_page_reference": "printed_page",
            "source_of_truth": "park",
            "source_files": {
                "park": str(PARK_PDF_PATH.relative_to(BASE_DIR)),
                "original": str(ORIGINAL_PDF_PATH.relative_to(BASE_DIR)),
            },
            "scope": {
                "park_pdf_page_start": INITIAL_SCOPE["park_pdf_page_start"],
                "park_pdf_page_end": INITIAL_SCOPE["park_pdf_page_end"],
                "original_pdf_page_start": INITIAL_SCOPE["original_pdf_page_start"],
                "original_pdf_page_end": INITIAL_SCOPE["original_pdf_page_end"],
            },
            "model": model,
        },
        "chapters": [
            {
                "chapter_id": chapter_meta["chapter_id"],
                "chapter_number": chapter_meta["chapter_number"],
                "title_by_version": chapter_meta["title_by_version"],
                "sections": sections,
            }
        ],
    }


def run(args: argparse.Namespace) -> dict[str, Any] | None:
    print(f"Rendering {PARK_PDF_PATH.name} pages {INITIAL_SCOPE['park_pdf_page_start']}-{INITIAL_SCOPE['park_pdf_page_end']} ...")
    park_pages = render_pdf_pages(
        PARK_PDF_PATH,
        pdf_page_start=INITIAL_SCOPE["park_pdf_page_start"],
        pdf_page_end=INITIAL_SCOPE["park_pdf_page_end"],
        cache_dir=args.cache_dir,
        zoom=args.zoom,
        force_refresh=args.force_refresh,
    )

    print(f"Rendering {ORIGINAL_PDF_PATH.name} pages {INITIAL_SCOPE['original_pdf_page_start']}-{INITIAL_SCOPE['original_pdf_page_end']} ...")
    original_pages = render_pdf_pages(
        ORIGINAL_PDF_PATH,
        pdf_page_start=INITIAL_SCOPE["original_pdf_page_start"],
        pdf_page_end=INITIAL_SCOPE["original_pdf_page_end"],
        cache_dir=args.cache_dir,
        zoom=args.zoom,
        force_refresh=args.force_refresh,
    )

    if args.render_only:
        print(f"Rendered {len(park_pages)} Park pages and {len(original_pages)} original pages.")
        return None

    api_key = os.environ.get(API_KEY_ENV)
    if not api_key:
        raise ExtractorError(f"Missing {API_KEY_ENV}. Set it before running Gemini extraction.")

    client = GeminiClient(
        api_key=api_key,
        model=args.model,
        cache_dir=args.cache_dir / "responses",
        force_refresh=args.force_refresh,
        max_retries=args.max_retries,
    )

    print("Extracting canonical Park units from page images ...")
    park_units = extract_park_units(client, park_pages)
    if not park_units:
        raise ExtractorError("Gemini did not return any Park units for the scoped pages.")

    print("Aligning original page images to the Park units ...")
    original_by_unit = align_original_to_park(client, original_pages, park_units)
    merged_units = attach_original_versions(park_units, original_by_unit)

    output = build_output(merged_units, args.model)
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output


def main() -> int:
    args = parse_args()
    try:
        output = run(args)
    except ExtractorError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if output is None:
        return 0

    total_units = sum(
        len(section["units"])
        for chapter in output["chapters"]
        for section in chapter["sections"]
    )
    print(f"Saved {total_units} units to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())