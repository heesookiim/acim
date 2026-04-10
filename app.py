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
                
                results.append({
                    "version": version,
                    "version_label": version_data["label"],
                    "document": doc_name,
                    "page": page_num,
                    "chapter": chapters[0] if chapters else None,
                    "section": canonical_primary_section if canonical_primary_section else (primary_section if primary_section else (sections[0] if sections else None)),
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


def get_reader_catalog():
    """Build a catalog of available chapters and sections from SEARCH_DATA."""
    catalog = {}
    
    # We will scan through original's pages to build the authoritative list
    orig_data = SEARCH_DATA.get("original", {}).get("documents", {}).get("교과서", {})
    pages = orig_data.get("pages", [])
    
    for page in pages:
        for item in page.get("contents", []):
            ch = item.get("chapter")
            sec = item.get("section")
            canonical_sec = canonicalize_section(ch, sec)
            if ch is not None:
                if str(ch) not in catalog:
                    catalog[str(ch)] = []
                if canonical_sec and canonical_sec not in catalog[str(ch)]:
                    catalog[str(ch)].append(canonical_sec)
                    
    # Format catalog as list of dicts for easier JS rendering
    formatted = []
    for ch_num in sorted(catalog.keys(), key=lambda x: int(x)):
        formatted.append({
            "chapter": ch_num,
            "sections": catalog[ch_num]
        })
    return formatted


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
    chapter = request.args.get("chapter")
    section = request.args.get("section")
    
    if not chapter:
        return jsonify({"error": "Missing chapter parameter"}), 400
        
    def get_section_contents(version_key):
        doc_data = SEARCH_DATA.get(version_key, {}).get("documents", {}).get("교과서", {})
        pages = doc_data.get("pages", [])
        
        contents = []
        for p in pages:
            page_contents = []
            matched_on_page = False
            for item in p.get("contents", []):
                # Match both chapter and section if section is provided
                match_ch = str(item.get("chapter")) == chapter
                item_section = item.get("section")
                canonical_item_section = canonicalize_section(item.get("chapter"), item_section)
                if section:
                    requested_section = canonicalize_section(chapter, section)
                    match_sec = canonical_item_section == requested_section
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
