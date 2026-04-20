#!/usr/bin/env python3
"""Parse PDF files using PDF.ai API and save structured JSON for the search engine."""

import argparse
import json
import os
import re
import sys
from collections import defaultdict

import requests
from dotenv import load_dotenv
from section_normalizer import canonicalize_section, clean_section_title, get_section_id

load_dotenv()

API_URL = "https://pdf.ai/api/v2/parse"
API_KEY = os.environ.get("PDF_AI_API_KEY", "")

VERSIONS = {
    "original": {
        "label": "원본",
        "files": {
            "교과서": {"path": "version/original/교과서_원본.json", "docId": "cmnt76zv0000clc046ksiaugl"},
            "교사용 지침서": {"path": "교사용_지침서_원본.json", "docId": "cmntcx2fl0025l3043zyamyui"},
            "학생용 연습서": {"path": "version/original/학생용_연습서_원본.json", "docId": "cmnteh3lv0000jl04ctrj6yeq"},
        },
    },
    "park": {
        "label": "박영수",
        "files": {
            "교과서": {
                "path": "version/park/교과서_박영수.pdf",
                "chunks": [
                    {"path": "version/park/splits/교과서_박영수_001.json", "docId": "cmnt9t1z7000xl304sedp8ntx", "start_page": 1, "end_page": 557},
                    {"path": "version/park/splits/교과서_박영수_002.json", "docId": "cmnt9z8su0013l304lbpquerl", "start_page": 558, "end_page": 619},
                ],
            },
            "교사용 지침서": {"path": "교사용_지침서_박영수.json", "docId": "cmntdwgof000fky043z4zf5xk"},
            "학생용 연습서": {"path": "version/park/학생용_연습서_박영수.json", "docId": "cmntevs9s0009jl04804d5ck1"},
        },
    },
    # "combined": {
    #     "label": "합본",
    #     "files": {
    #         "합본": {"path": "version/combined/합본.pdf", "docId": ""},
    #     },
    # },
}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Content types to discard from the output
DISCARD_TYPES = {"discarded"}


def normalize_front_matter_text(content):
    if not content:
        return content

    if re.fullmatch(r'머리말\s+서문', content):
        return "머리말"

    return content


def _check_api_key():
    if not API_KEY or API_KEY == "YOUR_KEY_HERE":
        print("  ERROR: Set PDF_AI_API_KEY in .env before running.")
        sys.exit(1)


def parse_pdf(pdf_path):
    """Upload a local PDF to PDF.ai and return the parsed response."""
    _check_api_key()
    headers = {"X-API-Key": API_KEY}
    with open(pdf_path, "rb") as f:
        files = [
            ("file", (os.path.basename(pdf_path), f, "application/pdf")),
            ("quality", (None, "standard")),
            ("lang_list", "en"),
        ]
        response = requests.post(API_URL, headers=headers, files=files)

    if not response.ok:
        print(f"  HTTP {response.status_code}: {response.text}")
    response.raise_for_status()
    return response.json()


def parse_by_docid(doc_id):
    """Parse an already-uploaded PDF using its docId."""
    _check_api_key()
    headers = {"X-API-Key": API_KEY}
    files = [
        ("docId", (None, doc_id)),
        ("quality", (None, "standard")),
        ("lang_list", "en"),
    ]
    response = requests.post(API_URL, headers=headers, files=files, timeout=300)

    if not response.ok:
        print(f"  HTTP {response.status_code}: {response.text}")
    response.raise_for_status()
    return response.json()


def normalize_title(title):
    """Backward-compatible title normalization used by parser internals."""
    cleaned = clean_section_title(title)
    if not cleaned:
        return ""
    return canonicalize_section(None, cleaned) or cleaned


def process_and_group(raw_contents):
    """Sort contents, tag with chapter/section, group by page, filter discarded."""
    
    # Store original index for stable sorting when bbox is missing
    for i, item in enumerate(raw_contents):
        item["_orig_index"] = i

    def get_sort_key(item):
        p = item.get("pageNumber") or 0
        bbox = item.get("bbox")
        y = bbox[1] if bbox and len(bbox) >= 2 else 0
        x = bbox[0] if bbox and len(bbox) >= 1 else 0
        return (p, y, x, item.get("_orig_index", 0))

    # Ensure items are processed in visual reading order (top to bottom)
    raw_contents.sort(key=get_sort_key)

    pages_map = defaultdict(list)
    current_chapter = None
    current_section_raw = None
    current_section = None
    current_section_id = None

    for item in raw_contents:
        item_type = item.get("type", "")
        content = item.get("content", "").strip()
        content = normalize_front_matter_text(content)
        page_num = item.get("pageNumber")

        if not page_num:
            continue

        is_front_matter_preface = (
            page_num <= 40
            and item_type in {"title", "text"}
            and content == "머리말"
        )
        if is_front_matter_preface:
            current_chapter = None
            current_section_raw = None
            current_section = None
            current_section_id = None

        # 1. Detect chapter (e.g. "제1장 기적에 대한 안내" or just "제29장 깨어나기")
        chapter_match = re.match(r'^제\s*\d+\s*장(?:[:\s]|$)', content)
        bbox = item.get("bbox", [])
        is_top_text_header = (
            item_type == "text"
            and chapter_match
            and len(bbox) >= 2
            and bbox[1] < 140
        )
        is_chapter_candidate = chapter_match and (item_type in {"title", "discarded"} or is_top_text_header)

        if is_chapter_candidate:
            # Skip discarded chapter titles at the bottom of the page (y > 750)
            # These are likely running headers for the NEXT page/chapter
            is_bottom_header = (item_type == "discarded" and len(bbox) >= 2 and bbox[1] > 750)
            
            if not is_bottom_header:
                ch_match = re.search(r'\d+', content)
                if ch_match:
                    detected_chapter = int(ch_match.group())
                    # Only reset section if this is a NEW chapter, not a running header
                    if detected_chapter != current_chapter:
                        current_chapter = detected_chapter
                        current_section_raw = None
                        current_section = None
                        current_section_id = None
                    else:
                        # Same chapter, just update current_chapter in case it was None
                        current_chapter = detected_chapter

        # 2. Detect section (e.g. "I. 서문", "1. 서문", or occasional "2 서문" in title)
        strict_section_match = re.match(r'^([IVXLC]+|\d+)\.\s+', content)
        title_section_match = re.match(r'^([IVXLC]+|\d+)(?:\.|\))?\s+', content)
        malformed_double_number_prefix = re.match(r'^([IVXLC]+|\d+)(?:\.|\))\s+\d+\s+', content)
        is_short_text_heading = (
            item_type == "text"
            and strict_section_match
            and len(content) <= 60
            and not malformed_double_number_prefix
        )
        if ((item_type == "title" and title_section_match and not malformed_double_number_prefix)
                or is_short_text_heading):
            candidate_raw = clean_section_title(content)
            candidate_section = canonicalize_section(current_chapter, candidate_raw)

            is_ch19_obstacle_subsection = (
                current_chapter == 19
                and current_section == "평화의 장애물"
                and candidate_raw is not None
                and candidate_raw.startswith(("첫", "둘", "셋", "넷"))
                and "장애" in candidate_raw
            )

            if not is_ch19_obstacle_subsection:
                current_section_raw = candidate_raw
                current_section = candidate_section
                current_section_id = get_section_id(current_chapter, current_section)

        # Skip discarded items from final output
        if item_type in DISCARD_TYPES:
            continue

        entry = {
            "type": item_type,
            "content": content,
            "chapter": current_chapter,
            "section_raw": current_section_raw,
            "section": current_section,
            "section_id": current_section_id,
        }
        if item_type == "image":
            entry["imageIds"] = item.get("imageIds", [])
            
        pages_map[page_num].append(entry)

    # Build final pages list
    pages = []
    for page_num in sorted(pages_map.keys()):
        page_contents = pages_map[page_num]
        
        # Use the chapter/section of the first content item on the page
        # This represents where the page starts, not all sections that appear on it
        first_chapter = None
        first_section = None
        if page_contents:
            first_chapter = page_contents[0].get("chapter")
            # For section, find the first non-null section on the page
            for item in page_contents:
                if item.get("section") is not None:
                    first_section = item["section"]
                    break
        
        # For backwards compatibility, also collect all unique chapters/sections
        chapters_on_page = []
        sections_on_page = []
        for c in page_contents:
            if c["chapter"] is not None and c["chapter"] not in chapters_on_page:
                chapters_on_page.append(c["chapter"])
            if c["section"] is not None and c["section"] not in sections_on_page:
                sections_on_page.append(c["section"])
                
        pages.append({
            "page": page_num,
            "chapters": chapters_on_page,
            "sections": sections_on_page,
            "primary_chapter": first_chapter,
            "primary_section": first_section,
            "contents": page_contents
        })

    return pages


def build_target_set(targets):
    """Build a set of (version_key, doc_name) pairs from CLI target arguments.

    Each target is matched against version labels and document names
    (case-insensitive, partial match).  For example:
        python parse_pdf.py 원본 "교사용 지침서"
    will match version "original" (label 원본) and doc "교사용 지침서".
    """
    if not targets:
        return None  # None means "run everything"

    matched = set()
    for version_key, version_info in VERSIONS.items():
        label = version_info["label"]
        # Check if any target matches this version label
        version_matched = any(t in label or t in version_key for t in targets)
        for doc_name in version_info["files"]:
            doc_matched = any(t in doc_name for t in targets)
            if version_matched and doc_matched:
                matched.add((version_key, doc_name))

    return matched


def list_available_targets():
    """Print all available version/document combinations."""
    print("Available targets (version label → document name):")
    for version_key, version_info in VERSIONS.items():
        label = version_info["label"]
        for doc_name in version_info["files"]:
            print(f"  {label} {doc_name}")


def main():
    parser = argparse.ArgumentParser(
        description="Parse PDFs using PDF.ai API.",
        epilog="Example: python parse_pdf.py 원본 \"교사용 지침서\"",
    )
    parser.add_argument(
        "targets", nargs="*",
        help="Version label and/or document name to filter. "
             "All words must match. Omit to parse everything.",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List available version/document targets and exit.",
    )
    args = parser.parse_args()

    if args.list:
        list_available_targets()
        return

    target_set = build_target_set(args.targets)
    if target_set is not None and not target_set:
        print("No matching version/document found for the given targets.")
        print()
        list_available_targets()
        return

    # Load existing parse_data.json to merge results (incremental updates)
    out_path = os.path.join(BASE_DIR, "parse_data.json")
    if os.path.exists(out_path):
        with open(out_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {}

    for version_key, version_info in VERSIONS.items():
        if version_key not in data:
            data[version_key] = {
                "label": version_info["label"],
                "documents": {},
            }
        for doc_name, file_info in version_info["files"].items():
            if target_set is not None and (version_key, doc_name) not in target_set:
                continue

            chunks = file_info.get("chunks")
            if chunks:
                # --- Chunked document ---
                print(f"Parsing (chunked): {doc_name}")
                all_raw_contents = []
                total_page_count = 0
                chunk_doc_ids = []

                for i, chunk in enumerate(chunks):
                    chunk_doc_id = chunk.get("docId")
                    chunk_path = chunk.get("path", "")
                    start_page = chunk["start_page"]
                    end_page = chunk["end_page"]
                    page_offset = start_page - 1  # chunk page 1 -> actual start_page

                    if chunk_path.endswith('.json') and os.path.exists(chunk_path):
                        print(f"  Chunk {i+1} (pages {start_page}-{end_page}): Using local JSON {chunk_path}")
                        with open(chunk_path, "r", encoding="utf-8") as f:
                            result = json.load(f)
                        if "contents" not in result:
                            result = {"contents": result, "pageCount": len(set(c.get("pageNumber") for c in result if c.get("pageNumber"))), "docId": chunk_doc_id or ""}
                    elif chunk_doc_id:
                        print(f"  Chunk {i+1} (pages {start_page}-{end_page}): docId={chunk_doc_id}")
                        result = parse_by_docid(chunk_doc_id)
                    else:
                        print(f"  Chunk {i+1} (pages {start_page}-{end_page}): no docId or path, skipping.")
                        continue

                    raw_contents = result.get("contents", [])
                    total_page_count += result.get("pageCount", 0)
                    chunk_doc_ids.append(chunk_doc_id)

                    for item in raw_contents:
                        pnum = item.get("pageNumber")
                        if pnum is not None:
                            item["pageNumber"] = pnum + page_offset
                    all_raw_contents.extend(raw_contents)

                all_pages = process_and_group(all_raw_contents)

                data[version_key]["documents"][doc_name] = {
                    "docId": ",".join(chunk_doc_ids),
                    "pageCount": total_page_count,
                    "pages": all_pages,
                }
                print(f"  -> {len(all_pages)} pages merged from {len(chunk_doc_ids)} chunk(s)")

            else:
                # --- Single document ---
                rel_path = file_info["path"]
                doc_id = file_info.get("docId", "")
                full_path = os.path.join(BASE_DIR, rel_path)
                print(f"Parsing: {doc_name}")

                if full_path.endswith('.json') and os.path.exists(full_path):
                    print(f"  Using local JSON file: {full_path}")
                    with open(full_path, "r", encoding="utf-8") as f:
                        result = json.load(f)
                    
                    # Fix result missing pageCount and docId
                    if "contents" not in result:
                        result = {"contents": result, "pageCount": len(set(c.get("pageNumber") for c in result if c.get("pageNumber"))), "docId": ""}
                elif doc_id:
                    print(f"  Using docId={doc_id}")
                    result = parse_by_docid(doc_id)
                else:
                    if not os.path.exists(full_path):
                        print(f"  WARNING: file not found and no docId, skipping.")
                        continue
                    print(f"  Uploading file: {full_path}")
                    result = parse_pdf(full_path)

                raw_contents = result.get("contents", [])
                page_count = result.get("pageCount", 0)
                result_doc_id = result.get("docId", doc_id)

                pages = process_and_group(raw_contents)
                data[version_key]["documents"][doc_name] = {
                    "docId": result_doc_id,
                    "pageCount": page_count,
                    "pages": pages,
                }
                print(f"  -> {len(pages)} pages parsed (pageCount={page_count}, docId={result_doc_id})")

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\nSaved to {out_path}")


if __name__ == "__main__":
    main()
