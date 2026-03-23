#!/usr/bin/env python3
"""Korean search engine for ACIM documents."""

import json
import os
import re
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Load search data once at startup
with open(os.path.join(BASE_DIR, "search_data.json"), "r", encoding="utf-8") as f:
    SEARCH_DATA = json.load(f)


def get_document_info():
    """Return version/document structure for the UI."""
    info = {}
    for version_key, version_data in SEARCH_DATA.items():
        docs = {}
        for doc_name, pages in version_data["documents"].items():
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
        pages = version_data["documents"].get(doc_name, [])
        for page_data in pages:
            page_num = page_data["page"]
            if page_num < page_from or page_num > page_to:
                continue

            text = page_data["text"]
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
                snippet = build_snippet(text, active_keywords)
                results.append({
                    "version": version,
                    "version_label": version_data["label"],
                    "document": doc_name,
                    "page": page_num,
                    "snippet": snippet,
                    "matched_keywords": keyword_matches,
                })

    return results


def build_snippet(text, keywords):
    """Build a snippet showing sentences that contain keywords, with highlights."""
    # Split text into sentences using Korean/general sentence boundaries
    sentences = re.split(r'(?<=[.!?。\n])\s*', text)
    # Filter out empty sentences
    sentences = [s.strip() for s in sentences if s.strip()]

    if not sentences:
        return text[:200] + ("..." if len(text) > 200 else "")

    # Find sentences containing any keyword, ordered by first keyword match
    matched_sentences = []
    for sentence in sentences:
        sentence_lower = sentence.lower()
        for kw in keywords:
            if kw.strip().lower() in sentence_lower:
                matched_sentences.append(sentence)
                break

    if not matched_sentences:
        return text[:200] + ("..." if len(text) > 200 else "")

    # Build snippet from matched sentences (limit to avoid huge output)
    parts = []
    for sentence in matched_sentences[:6]:
        chunk = sentence
        # Escape HTML
        chunk = chunk.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        # Highlight keywords
        for kw in keywords:
            pattern = re.compile(re.escape(kw), re.IGNORECASE)
            chunk = pattern.sub(lambda m: f'<mark>{m.group()}</mark>', chunk)
        parts.append(chunk)

    return "\n".join(parts)


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


if __name__ == "__main__":
    app.run(debug=False, port=5001)
