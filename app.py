#!/usr/bin/env python3
"""Korean search engine for ACIM documents."""

import json
import os
import re
from flask import Flask, render_template, request, jsonify
from section_normalizer import canonicalize_section

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Load search data once at startup
with open(os.path.join(BASE_DIR, "parse_data.json"), "r", encoding="utf-8") as f:
    SEARCH_DATA = json.load(f)


def get_document_info():
    """Return version/document structure for the UI."""
    info = {}
    for version_key, version_data in SEARCH_DATA.items():
        docs = {}
        for doc_name, doc_data in version_data["documents"].items():
            pages = doc_data.get("pages", [])
            page_numbers = [p["page"] for p in pages]
            docs[doc_name] = {
                "min_page": min(page_numbers) if page_numbers else 1,
                "max_page": max(page_numbers) if page_numbers else 1,
                "total_pages": len(page_numbers),
            }
        info[version_key] = {
            "label": version_data["label"],
            "documents": docs,
        }
    return info


def search_documents(version, documents, page_from, page_to, keywords, operator):
    """
    Search documents for keywords.
    
    keywords: list of up to 4 keyword strings
    operator: 'AND' or 'OR'
    """
    results = []
    version_data = SEARCH_DATA.get(version)
    if not version_data:
        return results

    for doc_name in documents:
        doc_data = version_data["documents"].get(doc_name, {})
        pages = doc_data.get("pages", []) if isinstance(doc_data, dict) else doc_data
        workbook_sections_by_page = {}
        if doc_name == "학생용 연습서":
            reader_pages = [p for p in pages if is_reader_page(doc_name, version, p)]
            labels_by_lesson = workbook_lesson_labels()
            workbook_sections_by_page = {
                page["page"]: labels_by_lesson.get(lessons[0])
                for page, lessons in workbook_page_lessons(reader_pages)
                if lessons
            }
        for page_data in pages:
            page_num = page_data["page"]
            if page_num < page_from or page_num > page_to:
                continue

            text = page_text_from_contents(page_data)
            text_lower = text.lower()

            # Check each keyword
            keyword_matches = []
            for kw in keywords:
                if not kw.strip():
                    continue
                kw_lower = kw.strip().lower()
                if kw_lower in text_lower:
                    keyword_matches.append(kw.strip())

            active_keywords = [kw.strip() for kw in keywords if kw.strip()]
            if not active_keywords:
                continue

            matched = False
            if operator == "AND":
                matched = len(keyword_matches) == len(active_keywords)
            else:  # OR
                matched = len(keyword_matches) > 0

            if matched:
                # Build highlighted snippet
                snippet = build_snippet(page_data, active_keywords)
                
                # Get primary chapter and section for this page
                chapters = page_data.get("chapters", [])
                sections = page_data.get("sections", [])
                primary_section = page_data.get("primary_section")
                primary_chapter = page_data.get("primary_chapter")
                canonical_primary_section = canonicalize_section(primary_chapter, primary_section)
                if not canonical_primary_section and sections:
                    canonical_primary_section = canonicalize_section(primary_chapter, sections[0])
                result_section = canonical_primary_section if canonical_primary_section else (primary_section if primary_section else (sections[0] if sections else None))
                if doc_name == "학생용 연습서":
                    result_section = workbook_sections_by_page.get(page_num, result_section)
                
                results.append({
                    "version": version,
                    "version_label": version_data["label"],
                    "document": doc_name,
                    "page": page_num,
                    "chapter": chapters[0] if chapters else None,
                    "section": result_section,
                    "snippet": snippet,
                    "matched_keywords": keyword_matches,
                })

    return results


def page_text_from_contents(page_data):
    """Build a single text string from structured page contents."""
    parts = []
    for item in page_data.get("contents", []):
        content = item.get("content", "")
        if content:
            parts.append(content)
    return "\n".join(parts)


def build_snippet(page_data, keywords):
    """Build a snippet showing content items that contain keywords, with highlights."""
    # Collect content items as individual blocks
    blocks = []
    for item in page_data.get("contents", []):
        content = item.get("content", "").strip()
        if not content:
            continue
        item_type = item.get("type", "text")
        # Split longer blocks into sentences for finer matching
        sentences = re.split(r'(?<=[.!?。\n])\s*', content)
        for s in sentences:
            s = s.strip()
            if s:
                blocks.append((item_type, s))

    if not blocks:
        text = page_text_from_contents(page_data)
        return text[:200] + ("..." if len(text) > 200 else "")

    # Find blocks containing any keyword
    matched_blocks = []
    for item_type, sentence in blocks:
        sentence_lower = sentence.lower()
        for kw in keywords:
            if kw.strip().lower() in sentence_lower:
                matched_blocks.append((item_type, sentence))
                break

    if not matched_blocks:
        text = page_text_from_contents(page_data)
        return text[:200] + ("..." if len(text) > 200 else "")

    # Build snippet from matched blocks (limit to avoid huge output)
    parts = []
    for item_type, sentence in matched_blocks[:6]:
        chunk = sentence
        # Escape HTML
        chunk = chunk.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        # Highlight keywords
        for kw in keywords:
            pattern = re.compile(re.escape(kw), re.IGNORECASE)
            chunk = pattern.sub(lambda m: f'<mark>{m.group()}</mark>', chunk)
        # Prefix title items with bold marker for richer display
        if item_type == "title":
            chunk = f'<strong>{chunk}</strong>'
        parts.append(chunk)

    return "\n".join(parts)


READER_DOCUMENTS = ("교과서", "교사용 지침서", "학생용 연습서")
READER_MIN_PAGES = {
    ("교과서", "original"): 13,
    ("교과서", "park"): 13,
    ("교사용 지침서", "original"): 4,
    ("교사용 지침서", "park"): 8,
    ("학생용 연습서", "original"): 15,
    ("학생용 연습서", "park"): 18,
}


TEACHER_GUIDE_SECTION_ALIASES = {
    "예수는 치유에서 특별한 역할을 하는가?": "예수는 치유에서 특별한 위치에 있는가?",
    "환생은 참인가?": "환생은 있는가?",
    "“심령” 능력은 바람직한가?": "심령 능력은 바람직한가?",
    "‘심령(psychic)’ 능력은 바람직한가?": "심령 능력은 바람직한가?",
    "죽음이란 무엇인가?": "죽음은 무엇인가?",
    "부활이란 무엇인가?": "부활은 무엇인가?",
    "난이도를 지각하는 것을 어떻게 피할 수 있는가?": "난이도에 대한 지각을 어떻게 피할 수 있는가?",
    "하느님의 교사는 생활환경에 변화를 요구받는가?": "신의 교사는 삶의 상황을 바꿔야 하는가?",
    "각 사람은 결국에 심판을 받을 것인가?": "모든 사람이 마지막에 심판받을 것인가?",
    "하느님의 교사는 학생의 마법 생각을 어떻게 다루는가?": "신의 교사는 마법 생각을 어떻게 다룰 것인가?",
    "정의란 무엇인가?": "정의는 무엇인가?",
    "치유와 속죄는 어떻게 관련되어 있는가?": "치유와 속죄는 어떤 관계인가?",
    "그 밖의 주제에 대해": "그 밖의 것에 대하여",
}


def reader_section_key(document, chapter, section):
    """Return a comparison key for reader section matching."""
    canonical = canonicalize_section(chapter, section) or section
    if not canonical:
        return None
    if document == "학생용 연습서":
        match = re.match(r"(\d+)과\b", canonical)
        if match:
            return f"lesson:{int(match.group(1))}"
        canonical = canonical.replace("하느님", "신")
        return re.sub(r"[“”\"'‘’()\[\]\s\xa0\-–—:：,.?…]+", "", canonical)
    if document == "교사용 지침서":
        canonical = TEACHER_GUIDE_SECTION_ALIASES.get(canonical, canonical)
        canonical = canonical.replace("하느님", "신")
        canonical = canonical.replace("생활환경", "삶의 상황")
        canonical = canonical.replace("직접 도달", "직접도달")
        canonical = re.sub(r"[“”\"'‘’()\[\]\s\xa0\-–—:：,.?…]+", "", canonical)
        return canonical
    return canonical


def is_reader_page(document, version_key, page):
    return page.get("page", 0) >= READER_MIN_PAGES.get((document, version_key), 1)


def clean_workbook_lesson_title(lesson_num, title):
    title = title.strip()
    title = re.sub(rf"^{lesson_num}과\s*", "", title)
    title = re.split(r"\s+\d+[.)]\s+", title, maxsplit=1)[0]
    title = title.strip(" ”\"")
    title = re.sub(r"\s+", " ", title)
    title = re.sub(r"\s+\d+$", "", title).strip()
    if lesson_num < 1 or lesson_num > 360:
        return None
    if not title or title.startswith("~") or re.fullmatch(r"[\d~\s]+", title):
        return None
    return title


def workbook_lesson_headings(page, strict=True):
    """Extract workbook lesson headings from page text."""
    text = page_text_from_contents(page)
    text = re.sub(r"\s+", " ", text).strip()
    headings = []
    if strict:
        pattern = re.compile(
            r"(\d{1,3})과\s+(?:\1과\s+)?[“\"]?(.{3,120}?)[”\"]?(?=\s+\d+\s| \d{1,3}과\b|$)"
        )
    else:
        pattern = re.compile(
            r"(\d{1,3})과\s+(?:\1과\s+)?[“\"]?(.{3,180}?)[”\"]?(?=\s+\d+[.)]?\s+|\s+\d{1,3}과\b|$)"
        )
    for match in pattern.finditer(text):
        lesson_num = int(match.group(1))
        title = clean_workbook_lesson_title(lesson_num, match.group(2))
        if not title:
            continue
        headings.append((lesson_num, f"{lesson_num}과 {title}"))
    return headings


def workbook_lesson_numbers(page):
    """Return all workbook lesson numbers that visibly start on a page."""
    text = page_text_from_contents(page)
    numbers = []
    seen = set()
    for match in re.finditer(r"(?<!\d)(\d{1,3})과\b", text):
        lesson_num = int(match.group(1))
        if 1 <= lesson_num <= 360 and lesson_num not in seen:
            seen.add(lesson_num)
            numbers.append(lesson_num)
    return numbers


def workbook_lesson_labels():
    """Build labels for every workbook lesson from the best available extraction."""
    candidates = {}
    seen_numbers = set()
    for version_key in ("original", "park"):
        pages = SEARCH_DATA.get(version_key, {}).get("documents", {}).get("학생용 연습서", {}).get("pages", [])
        for page in pages:
            page_num = page.get("page", 0)
            is_toc_page = page_num <= 8
            is_body_page = is_reader_page("학생용 연습서", version_key, page)
            if is_toc_page or is_body_page:
                for lesson_num, label in workbook_lesson_headings(page, strict=False):
                    candidates.setdefault(lesson_num, []).append(label)
            for lesson_num in workbook_lesson_numbers(page):
                seen_numbers.add(lesson_num)

    def label_score(label):
        title = label.split("과", 1)[1].strip() if "과" in label else label
        penalty = 0
        if re.match(r"\d+[.)]", title):
            penalty += 1000
        if "[" in title:
            penalty += 100
        return penalty + len(label)

    return {
        lesson_num: min(candidates.get(lesson_num, [f"{lesson_num}과"]), key=label_score)
        for lesson_num in sorted(seen_numbers)
    }


def workbook_page_lessons(pages):
    """Map workbook pages to all lesson numbers active on that page."""
    current_lesson = None
    mapped = []
    for page in pages:
        text = page_text_from_contents(page)
        matches = [
            match for match in re.finditer(r"(?<!\d)(\d{1,3})과\b", text)
            if 1 <= int(match.group(1)) <= 360
        ]
        lessons = []
        seen = set()
        if current_lesson and matches and matches[0].start() > 20:
            lessons.append(current_lesson)
            seen.add(current_lesson)
        for match in matches:
            lesson_num = int(match.group(1))
            if lesson_num not in seen:
                lessons.append(lesson_num)
                seen.add(lesson_num)
        if lessons:
            current_lesson = lessons[-1]
            mapped.append((page, lessons))
        else:
            mapped.append((page, [current_lesson] if current_lesson else []))
    return mapped


def get_reader_catalog():
    """Build a catalog of available reader documents, chapters, and sections."""
    catalog_by_doc = {}
    
    for doc_name in READER_DOCUMENTS:
        catalog = {}
        section_keys = set()
        has_chapters = False

        for version_key in ("original", "park"):
            doc_data = SEARCH_DATA.get(version_key, {}).get("documents", {}).get(doc_name, {})
            for page in doc_data.get("pages", []):
                if doc_name == "학생용 연습서":
                    ch_key = ""
                    catalog.setdefault(ch_key, [])
                    for lesson_num, label in workbook_lesson_headings(page):
                        section_key = (ch_key, f"lesson:{lesson_num}")
                        if section_key not in section_keys:
                            section_keys.add(section_key)
                            catalog[ch_key].append(label)
                    continue
                if not is_reader_page(doc_name, version_key, page):
                    continue
                for item in page.get("contents", []):
                    ch = item.get("chapter")
                    sec = item.get("section")
                    canonical_sec = canonicalize_section(ch, sec) or sec
                    if ch is not None:
                        has_chapters = True
                    ch_key = str(ch) if ch is not None else ""
                    if ch_key == "0":
                        continue
                    if ch_key not in catalog:
                        catalog[ch_key] = []
                    sec_key = reader_section_key(doc_name, ch, canonical_sec)
                    section_key = (ch_key, sec_key)
                    if canonical_sec and section_key not in section_keys:
                        section_keys.add(section_key)
                        catalog[ch_key].append(canonical_sec)

        def chapter_sort_key(ch_num):
            if ch_num == "":
                return -1
            try:
                return int(ch_num)
            except ValueError:
                return 9999

        if doc_name == "학생용 연습서":
            labels_by_lesson = workbook_lesson_labels()
            catalog[""] = [labels_by_lesson.get(n, f"{n}과") for n in range(1, 361)]

        catalog_by_doc[doc_name] = {
            "has_chapters": has_chapters,
            "chapters": [
                {"chapter": ch_num, "sections": catalog[ch_num]}
                for ch_num in sorted(catalog.keys(), key=chapter_sort_key)
            ],
        }

    return catalog_by_doc


@app.route("/")
def index():
    doc_info = get_document_info()
    return render_template("index.html", doc_info=doc_info)


@app.route("/api/search", methods=["POST"])
def api_search():
    data = request.get_json()
    version = data.get("version", "original")
    documents = data.get("documents", [])
    page_from = int(data.get("page_from", 1))
    page_to = int(data.get("page_to", 9999))
    keywords = data.get("keywords", [])
    operator = data.get("operator", "AND")

    # Validate inputs
    if operator not in ("AND", "OR"):
        operator = "AND"
    keywords = [str(k) for k in keywords[:4]]  # max 4 keywords

    results = search_documents(version, documents, page_from, page_to, keywords, operator)
    return jsonify({"results": results, "count": len(results)})


@app.route("/reader")
def reader():
    catalog = get_reader_catalog()
    return render_template("reader.html", catalog=catalog)


@app.route("/api/reader/section")
def api_reader_section():
    document = request.args.get("document", "교과서")
    chapter = request.args.get("chapter")
    section = request.args.get("section")
    
    if document not in READER_DOCUMENTS:
        return jsonify({"error": "Unsupported document"}), 400
    if document == "교과서" and not chapter:
        return jsonify({"error": "Missing chapter parameter"}), 400
        
    def get_section_contents(version_key):
        doc_data = SEARCH_DATA.get(version_key, {}).get("documents", {}).get(document, {})
        pages = doc_data.get("pages", [])
        requested_section_key = reader_section_key(document, chapter, section)

        if document == "학생용 연습서" and requested_section_key:
            lesson_match = re.match(r"lesson:(\d+)", requested_section_key)
            if lesson_match:
                lesson_num = int(lesson_match.group(1))
                contents = []
                reader_pages = [p for p in pages if is_reader_page(document, version_key, p)]
                for p, active_lessons in workbook_page_lessons(reader_pages):
                    if lesson_num in active_lessons:
                        contents.append({
                            "page": p["page"],
                            "items": [
                                item for item in p.get("contents", [])
                                if item.get("content") and item.get("type") != "discarded"
                            ],
                        })
                return contents
        
        contents = []
        for p in pages:
            if not is_reader_page(document, version_key, p):
                continue
            page_contents = []
            matched_on_page = False
            for item in p.get("contents", []):
                # Match both chapter and section if section is provided
                match_ch = True if not chapter else str(item.get("chapter")) == chapter
                item_section = item.get("section")
                canonical_item_section = canonicalize_section(item.get("chapter"), item_section) or item_section
                if section:
                    item_section_key = reader_section_key(document, item.get("chapter"), canonical_item_section)
                    match_sec = item_section_key == requested_section_key
                else:
                    match_sec = True
                
                if match_ch and match_sec:
                    page_contents.append(item)
                    matched_on_page = True
            
            if matched_on_page:
                contents.append({
                    "page": p["page"],
                    "items": page_contents
                })
        return contents

    original_contents = get_section_contents("original")
    park_contents = get_section_contents("park")
    
    return jsonify({
        "original": original_contents,
        "park": park_contents
    })


if __name__ == "__main__":
    app.run(debug=False, port=5001)
