#!/usr/bin/env python3
"""Audit and repair parse_data.json using local PDF text.

The repair keeps parse_data.json as the baseline structure and writes a separate
parse_data.repaired.json by replacing page text with cleaned local PDF text.
"""

import argparse
import copy
import json
import os
import re
import unicodedata
from collections import Counter, defaultdict

import fitz  # PyMuPDF

from section_normalizer import canonicalize_section, get_section_id


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_INPUT = os.path.join(BASE_DIR, "parse_data.json")
DEFAULT_OUTPUT = os.path.join(BASE_DIR, "parse_data.repaired.json")
DEFAULT_LOCAL_OUTPUT = os.path.join(BASE_DIR, "parse_data.local.json")

PDF_PATHS = {
    ("original", "교과서"): "version/original/교과서_원본.pdf",
    ("original", "교사용 지침서"): "version/original/교사용_지침서_원본.pdf",
    ("original", "학생용 연습서"): "version/original/학생용_연습서_원본.pdf",
    ("park", "교과서"): "version/park/교과서_박영수.pdf",
    ("park", "교사용 지침서"): "version/park/교사용지침서_박영수.pdf",
    ("park", "학생용 연습서"): "version/park/연습서_박영수.pdf",
}

HIDDEN_CHARS = {
    "\u00a0": "NO-BREAK SPACE",
    "\u00ad": "SOFT HYPHEN",
    "\u200b": "ZERO WIDTH SPACE",
    "\u200c": "ZERO WIDTH NON-JOINER",
    "\u200d": "ZERO WIDTH JOINER",
    "\ufeff": "ZERO WIDTH NO-BREAK SPACE/BOM",
}

GARBLE_PATTERNS = {
    "latex_or_tex": re.compile(r"\\[A-Za-z]+|\^\s*\{|_\s*\{"),
    "brace_pipe_digit_cluster": re.compile(
        r"(?:[A-Za-z0-9. ]*[{}|\\][A-Za-z0-9. {}|\\]{2,}|"
        r"[A-Za-z]\s+[A-Za-z]\s*_\s*\{[^}]+\})"
    ),
    "long_digit_in_korean_prose": re.compile(r"(?<![\d-])\d{6,}(?![\d-])"),
}

KNOWN_BAD_FRAGMENTS = [
    "3}5|7]017}",
    "7700.40",
    "740172721722",
    "o k _ { 2 } = 1",
    r"^ { o \bar { } }",
]

EXPECTED_PHRASES = [
    "존재",
    "특별",
    "하느님과 다르게 사랑하려",
]

HANGUL = r"[\uAC00-\uD7A3]"
MID_WORD_BREAK = re.compile(f"({HANGUL})\n({HANGUL})")
PAGE_NUMBER_MARKER = re.compile(r"\s*-\s*\d+\s*-\s*")
RUNNING_HEADER = re.compile(r"^제\d+장[^\n]{0,40}\n")


def normalize_text(text):
    """Normalize hidden/layout chars and PDF line wrapping."""
    if not text:
        return ""

    text = unicodedata.normalize("NFC", text)
    text = text.replace("\u00a0", " ")
    text = text.replace("\u00ad", "")
    for ch in ("\u200b", "\u200c", "\u200d", "\ufeff"):
        text = text.replace(ch, "")

    text = PAGE_NUMBER_MARKER.sub(" ", text)
    text = RUNNING_HEADER.sub("", text)

    prev = None
    while prev != text:
        prev = text
        text = MID_WORD_BREAK.sub(r"\1\2", text)

    text = re.sub(r" \n", " ", text)
    text = re.sub(r"(?<![.!?。:\n])\n(?!\n)", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_blocks(text):
    """Split cleaned page text into stable, searchable blocks."""
    if not text:
        return []
    blocks = [b.strip() for b in re.split(r"\n+", text) if b.strip()]
    if len(blocks) > 1:
        return blocks
    sentences = re.split(r"(?<=[.!?。])\s+", text)
    return [s.strip() for s in sentences if s.strip()] or [text]


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def page_text(page):
    return "\n".join(
        item.get("content", "")
        for item in page.get("contents", [])
        if item.get("content")
    )


def all_text(data):
    parts = []
    for version_data in data.values():
        for doc_data in version_data.get("documents", {}).values():
            for page in doc_data.get("pages", []):
                parts.append(page_text(page))
    return "\n".join(parts)


def is_long_digit_false_positive(content, match):
    window = content[max(0, match.start() - 50): match.end() + 50]
    if not re.search(HANGUL, window):
        return True
    return bool(
        re.search(
            r"ISBN|www\.|@|년|페이지|등록|전화|주소|\d{4}\.\d{2}\.\d{2}|\d{2,3}-\d",
            window,
        )
    )


def audit_data(data):
    """Return deterministic counters and samples for text defects."""
    summary = defaultdict(Counter)
    samples = defaultdict(list)
    fixtures = Counter()

    for version_key, version_data in data.items():
        for doc_name, doc_data in version_data.get("documents", {}).items():
            pages_by_kind = defaultdict(set)
            for page in doc_data.get("pages", []):
                page_num = page.get("page")
                for item_index, item in enumerate(page.get("contents", [])):
                    content = item.get("content", "") or ""
                    if not content:
                        continue

                    for ch, label in HIDDEN_CHARS.items():
                        count = content.count(ch)
                        if count:
                            kind = f"hidden:{label}"
                            summary[(version_key, doc_name)][f"{kind}:chars"] += count
                            summary[(version_key, doc_name)][f"{kind}:items"] += 1
                            pages_by_kind[kind].add(page_num)
                            add_sample(samples, kind, version_key, doc_name, page_num, item_index, content)

                    for kind, pattern in GARBLE_PATTERNS.items():
                        match = pattern.search(content)
                        if not match:
                            continue
                        if kind == "long_digit_in_korean_prose" and is_long_digit_false_positive(content, match):
                            continue
                        summary[(version_key, doc_name)][f"garble:{kind}:items"] += 1
                        pages_by_kind[f"garble:{kind}"].add(page_num)
                        add_sample(samples, f"garble:{kind}", version_key, doc_name, page_num, item_index, content, match)

                    for fragment in KNOWN_BAD_FRAGMENTS:
                        if fragment in content:
                            fixtures[f"bad_fragment:{fragment}"] += 1

            for kind, pages in pages_by_kind.items():
                summary[(version_key, doc_name)][f"{kind}:pages"] = len(pages)
            summary[(version_key, doc_name)]["total_pages"] = len(doc_data.get("pages", []))

    text = all_text(data)
    for phrase in EXPECTED_PHRASES:
        fixtures[f"phrase_present:{phrase}"] = int(phrase in text)

    return summary, samples, fixtures


def add_sample(samples, kind, version_key, doc_name, page_num, item_index, content, match=None):
    key = (kind, version_key, doc_name)
    if len(samples[key]) >= 5:
        return
    if match:
        start = max(0, match.start() - 80)
        end = min(len(content), match.end() + 120)
        snippet = content[start:end]
    else:
        snippet = content[:220]
    snippet = snippet.replace("\n", "\\n").replace("\u00a0", "[NBSP]").replace("\u00ad", "[SHY]")
    samples[key].append({"page": page_num, "item": item_index, "snippet": snippet})


def extract_local_pages(pdf_path):
    doc = fitz.open(pdf_path)
    try:
        return {
            page_index + 1: normalize_text(page.get_text())
            for page_index, page in enumerate(doc)
        }
    finally:
        doc.close()


def build_local_data(baseline):
    """Build a local-PDF-only artifact in parse_data-like shape."""
    local_data = {}
    missing_pdfs = []

    for version_key, version_data in baseline.items():
        local_data[version_key] = {
            "label": version_data.get("label", version_key),
            "documents": {},
        }
        for doc_name in version_data.get("documents", {}):
            rel_path = PDF_PATHS.get((version_key, doc_name))
            if not rel_path:
                missing_pdfs.append((version_key, doc_name, "<not configured>"))
                continue

            pdf_path = os.path.join(BASE_DIR, rel_path)
            if not os.path.exists(pdf_path):
                missing_pdfs.append((version_key, doc_name, rel_path))
                continue

            local_pages = extract_local_pages(pdf_path)
            pages = []
            for page_num in sorted(local_pages):
                contents = [
                    {
                        "type": "text",
                        "content": block,
                        "chapter": None,
                        "section_raw": None,
                        "section": None,
                        "section_id": None,
                    }
                    for block in split_blocks(local_pages[page_num])
                ]
                pages.append({
                    "page": page_num,
                    "chapters": [],
                    "sections": [],
                    "primary_chapter": None,
                    "primary_section": None,
                    "contents": contents,
                })

            local_data[version_key]["documents"][doc_name] = {
                "docId": f"local:{rel_path}",
                "pageCount": len(local_pages),
                "pages": pages,
            }

    return local_data, missing_pdfs


def repair_page(page, local_text, state):
    repaired = copy.deepcopy(page)
    existing_contents = page.get("contents", [])
    default_chapter, default_section = page_defaults(page)

    if local_text:
        contents = []
        current_chapter = default_chapter if default_chapter is not None else state.get("chapter")
        current_section = default_section if default_section is not None else state.get("section")
        current_section_raw = default_section if default_section is not None else state.get("section_raw")
        current_section_id = get_section_id(current_chapter, current_section)

        for block in split_blocks(local_text):
            contents.append({
                "type": "text",
                "content": block,
                "chapter": current_chapter,
                "section_raw": current_section_raw,
                "section": current_section,
                "section_id": current_section_id,
            })

        if contents:
            repaired["contents"] = contents
            state.update({
                "chapter": contents[-1].get("chapter"),
                "section": contents[-1].get("section"),
                "section_raw": contents[-1].get("section_raw"),
                "section_id": contents[-1].get("section_id"),
            })
    else:
        for item in existing_contents:
            if item.get("content"):
                item["content"] = normalize_text(item["content"])
        repaired["contents"] = existing_contents

    refresh_page_metadata(repaired)
    return repaired


def page_defaults(page):
    chapter = page.get("primary_chapter")
    section = page.get("primary_section")

    if chapter is None:
        for item in page.get("contents", []):
            if item.get("chapter") is not None:
                chapter = item.get("chapter")
                break

    if section is None:
        for item in page.get("contents", []):
            if item.get("section") is not None:
                section = item.get("section")
                break

    return chapter, canonicalize_section(chapter, section)


def refresh_page_metadata(page):
    chapters = []
    sections = []
    first_chapter = None
    first_section = None

    for item in page.get("contents", []):
        chapter = item.get("chapter")
        section = item.get("section")
        if first_chapter is None and chapter is not None:
            first_chapter = chapter
        if first_section is None and section is not None:
            first_section = section
        if chapter is not None and chapter not in chapters:
            chapters.append(chapter)
        if section is not None and section not in sections:
            sections.append(section)

    page["chapters"] = chapters
    page["sections"] = sections
    page["primary_chapter"] = first_chapter
    page["primary_section"] = first_section


def repair_data(data):
    repaired = copy.deepcopy(data)
    missing_pdfs = []

    for version_key, version_data in repaired.items():
        for doc_name, doc_data in version_data.get("documents", {}).items():
            rel_path = PDF_PATHS.get((version_key, doc_name))
            if not rel_path:
                missing_pdfs.append((version_key, doc_name, "<not configured>"))
                continue

            pdf_path = os.path.join(BASE_DIR, rel_path)
            if not os.path.exists(pdf_path):
                missing_pdfs.append((version_key, doc_name, rel_path))
                continue

            local_pages = extract_local_pages(pdf_path)
            state = {"chapter": None, "section": None, "section_raw": None, "section_id": None}
            repaired_pages = []
            for page in doc_data.get("pages", []):
                page_num = page.get("page")
                repaired_pages.append(repair_page(page, local_pages.get(page_num, ""), state))
            doc_data["pages"] = repaired_pages
            doc_data["pageCount"] = len(local_pages)

    return repaired, missing_pdfs


def print_audit(title, summary, samples, fixtures):
    print(f"\n=== {title} ===")
    for version_key, doc_name in sorted(summary):
        print(f"\n{version_key} / {doc_name}")
        for key, value in summary[(version_key, doc_name)].most_common():
            print(f"  {key}: {value}")

    print("\nFixtures")
    for key in sorted(fixtures):
        print(f"  {key}: {fixtures[key]}")

    print("\nSamples")
    for key in sorted(samples):
        kind, version_key, doc_name = key
        print(f"\n[{kind}] {version_key} / {doc_name}")
        for sample in samples[key]:
            print(f"  page {sample['page']}, item {sample['item']}: {sample['snippet']}")


def main():
    parser = argparse.ArgumentParser(description="Audit and repair parse_data.json without overwriting it.")
    parser.add_argument("--input", default=DEFAULT_INPUT, help="Input parse_data JSON path.")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Repaired JSON output path.")
    parser.add_argument("--local-output", default=DEFAULT_LOCAL_OUTPUT, help="Local PDF extraction artifact path.")
    parser.add_argument("--audit-only", action="store_true", help="Only print the audit report.")
    parser.add_argument("--skip-local-output", action="store_true", help="Do not write parse_data.local.json.")
    parser.add_argument("--quiet-samples", action="store_true", help="Do not print sample snippets.")
    args = parser.parse_args()

    data = load_json(args.input)
    before_summary, before_samples, before_fixtures = audit_data(data)
    if args.quiet_samples:
        before_samples = {}
    print_audit("BEFORE", before_summary, before_samples, before_fixtures)

    if args.audit_only:
        return

    if not args.skip_local_output:
        local_data, local_missing_pdfs = build_local_data(data)
        write_json(args.local_output, local_data)
        print(f"\nWrote local extraction data to {args.local_output}")
        if local_missing_pdfs:
            print("\nMissing local PDF mappings/files:")
            for version_key, doc_name, path in local_missing_pdfs:
                print(f"  {version_key} / {doc_name}: {path}")

    repaired, missing_pdfs = repair_data(data)
    write_json(args.output, repaired)
    print(f"\nWrote repaired data to {args.output}")
    if missing_pdfs:
        print("\nMissing PDF mappings/files:")
        for version_key, doc_name, path in missing_pdfs:
            print(f"  {version_key} / {doc_name}: {path}")

    after_summary, after_samples, after_fixtures = audit_data(repaired)
    if args.quiet_samples:
        after_samples = {}
    print_audit("AFTER", after_summary, after_samples, after_fixtures)


if __name__ == "__main__":
    main()
