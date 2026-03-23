#!/usr/bin/env python3
"""Extract text from PDF files and save as JSON for the search engine."""

import json
import os
import re
import sys
import fitz  # PyMuPDF


VERSIONS = {
    "original": {
        "label": "원본",
        "files": {
            "교과서": "version/original/교과서_원본.pdf",
            "교사용 지침서": "version/original/교사용_지침서_원본.pdf",
            "학생용 연습서": "version/original/학생용_연습서_원본.pdf",
        },
    },
    "park": {
        "label": "박영수",
        "files": {
            "교과서": "version/park/교과서_박영수.pdf",
            "교사용 지침서": "version/park/교사용_지침서_박영수.pdf",
            "학생용 연습서": "version/park/학생용_연습서_박영수.pdf",
        },
    },
    "combined": {
        "label": "합본",
        "files": {
            "합본": "version/combined/합본.pdf",
        },
    },
}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Hangul syllable range: AC00-D7A3
_HANGUL = r'[\uAC00-\uD7A3]'
_MID_WORD_BREAK = re.compile(f'({_HANGUL})\n({_HANGUL})')


def clean_text(text):
    """Remove spurious newlines introduced by PDF line wrapping."""
    # 1. Remove \n that splits a Korean word (Hangul + \n + Hangul)
    #    Run in a loop because re.sub skips overlapping matches.
    prev = None
    while prev != text:
        prev = text
        text = _MID_WORD_BREAK.sub(r'\1\2', text)
    # 2. Remove \n after a trailing space (word-boundary line wrap)
    text = re.sub(r' \n', ' ', text)
    # 3. Replace remaining single \n with a space (general line wrap),
    #    but keep \n after sentence-ending punctuation for paragraph structure.
    text = re.sub(r'(?<![.\n])\n(?!\n)', ' ', text)
    # 4. Remove embedded PDF page numbers like "- 6 -" or "- 15 -"
    text = re.sub(r'\s*-\s*\d+\s*-\s*', ' ', text)
    # 5. Remove duplicate numbering: "N. N " → "N. " (original version artifact)
    text = re.sub(r'(\d+)\.\s*\1\s', r'\1. ', text)
    # 4b. Remove duplicate numbering without dot: "N N " → "N " (e.g. "4 4 기적은")
    text = re.sub(r'(?<!\d)(\d+)\s+\1\s+', r'\1 ', text)
    # 5. Also handle "N. N)" pattern like "1.14)" → keep as-is (not a duplicate)
    # 6. Insert newline before numbered items that follow sentence-ending punctuation
    #    e.g., "...이다. 3 기적은" → "...이다.\n3 기적은"
    text = re.sub(r'([.!?。])\s+(\d{1,3}\s+[^\d])', r'\1\n\2', text)
    # 7. Insert newline before chapter headings (제N장)
    text = re.sub(r'[ \t\xa0]+(제\d+장)', r'\n\1', text)
    # 8. Insert newline before Roman numeral section headers (I., II., etc.)
    text = re.sub(r'[ \t\xa0]+([IVXL]+\.[ \t\xa0])', r'\n\1', text)
    # 9. Insert newline before numbered section headers (N. Korean-title)
    text = re.sub(r'[ \t\xa0]+(\d+\.[ \t\xa0]+[\uAC00-\uD7A3])', r'\n\1', text)
    # 10. Insert newline before bare content number after Korean text
    text = re.sub(r'([\uAC00-\uD7A3])[ \t\xa0]+(\d{1,3}[ \t\xa0]+[\uAC00-\uD7A3])', r'\1\n\2', text)
    return text


def extract_pdf(pdf_path):
    """Extract text from a PDF, returning a list of {page, text} dicts."""
    pages = []
    doc = fitz.open(pdf_path)
    for page_num in range(len(doc)):
        page = doc[page_num]
        text = page.get_text()
        if text.strip():
            pages.append({"page": page_num + 1, "text": clean_text(text)})
    doc.close()
    return pages


def strip_running_headers(pages):
    """Remove PDF running headers (e.g. 제N장 TITLE) from page starts.

    Detects headers by grouping pages that start with the same 제N장 prefix
    and finding the common text prefix across those pages.
    """
    # Group pages by their 제N장 prefix
    groups = {}
    for p in pages:
        m = re.match(r'(제\d+장)', p['text'])
        if m:
            key = m.group(1)
            groups.setdefault(key, []).append(p)

    # For each group with 2+ pages, find common prefix = running header
    for key, group_pages in groups.items():
        if len(group_pages) < 2:
            continue
        texts = [p['text'] for p in group_pages]
        prefix = texts[0]
        for t in texts[1:]:
            i = 0
            while i < len(prefix) and i < len(t) and prefix[i] == t[i]:
                i += 1
            prefix = prefix[:i]
        prefix = prefix.rstrip()
        if len(prefix) <= len(key) + 1:
            continue
        for p in group_pages:
            if p['text'].startswith(prefix):
                rest = p['text'][len(prefix):]
                rest = rest.lstrip(' \t\xa0')
                if rest.startswith('\n'):
                    rest = rest[1:]
                p['text'] = rest
    return pages


def main():
    data = {}
    for version_key, version_info in VERSIONS.items():
        data[version_key] = {
            "label": version_info["label"],
            "documents": {},
        }
        for doc_name, rel_path in version_info["files"].items():
            full_path = os.path.join(BASE_DIR, rel_path)
            print(f"Extracting: {full_path}")
            if not os.path.exists(full_path):
                print(f"  WARNING: file not found, skipping.")
                continue
            pages = extract_pdf(full_path)
            pages = strip_running_headers(pages)
            data[version_key]["documents"][doc_name] = pages
            print(f"  -> {len(pages)} pages extracted")

    out_path = os.path.join(BASE_DIR, "search_data.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\nSaved to {out_path}")


if __name__ == "__main__":
    main()
