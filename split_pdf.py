#!/usr/bin/env python3
"""Split a PDF into chunks that each stay under a size limit (default 4.5 MB)."""

import argparse
import math
import os
import re

import fitz  # PyMuPDF


def split_pdf(input_path, output_dir, max_size_mb=4.5, page_range=None, force_chunk_pages=None):
    """Split a PDF into chunks under max_size_mb.

    Args:
        input_path: Path to the source PDF.
        output_dir: Directory for output chunks.
        max_size_mb: Maximum file size per chunk in MB.
        page_range: Optional (start, end) 1-indexed inclusive range to limit pages.

    Returns:
        List of dicts: [{"path": ..., "start_page": ..., "end_page": ...}, ...]
    """
    doc = fitz.open(input_path)
    total_pages = len(doc)
    file_size = os.path.getsize(input_path)
    basename = os.path.splitext(os.path.basename(input_path))[0]

    # Determine page range
    if page_range:
        first, last = page_range
        first = max(1, first)
        last = min(total_pages, last)
    else:
        first, last = 1, total_pages

    num_pages = last - first + 1

    max_bytes = max_size_mb * 1_000_000  # decimal MB to match Finder / API limits
    if force_chunk_pages:
        pages_per_chunk = force_chunk_pages
    else:
        # Estimate pages per chunk from average page size
        avg_page_bytes = file_size / total_pages
        pages_per_chunk = max(1, int(math.floor(max_bytes / avg_page_bytes)))
        # Add safety margin (actual sizes vary)
        pages_per_chunk = max(1, pages_per_chunk - 5)

    print(f"Source: {input_path}")
    print(f"  Total pages: {total_pages}, File size: {file_size / 1_000_000:.1f} MB")
    print(f"  Splitting pages {first}-{last} ({num_pages} pages)")
    print(f"  Estimated ~{pages_per_chunk} pages/chunk (target < {max_size_mb} MB)")

    os.makedirs(output_dir, exist_ok=True)

    chunks = []
    chunk_num = 0
    page_idx = first - 1  # 0-indexed

    while page_idx < last:
        chunk_num += 1
        chunk_start = page_idx  # 0-indexed
        chunk_end = min(page_idx + pages_per_chunk, last)  # exclusive 0-indexed

        out_doc = fitz.open()
        out_doc.insert_pdf(doc, from_page=chunk_start, to_page=chunk_end - 1)

        out_name = f"{basename}_{chunk_num:03d}.pdf"
        out_path = os.path.join(output_dir, out_name)
        out_doc.save(out_path)
        out_size = os.path.getsize(out_path)
        out_doc.close()

        # If chunk exceeds limit and has more than 1 page, re-split with fewer pages
        if out_size > max_bytes and (chunk_end - chunk_start) > 1:
            os.remove(out_path)
            # Scale down proportionally based on actual vs target size
            ratio = max_bytes / out_size
            pages_per_chunk = max(1, int((chunk_end - chunk_start) * ratio) - 2)
            chunk_num -= 1
            print(f"  Chunk too large ({out_size / 1_000_000:.1f} MB), retrying with {pages_per_chunk} pages/chunk")
            continue

        start_page_1indexed = chunk_start + 1
        end_page_1indexed = chunk_end
        chunks.append({
            "path": out_path,
            "start_page": start_page_1indexed,
            "end_page": end_page_1indexed,
        })
        print(f"  {out_name}: pages {start_page_1indexed}-{end_page_1indexed} ({out_size / 1_000_000:.2f} MB)")

        page_idx = chunk_end

    doc.close()
    print(f"\n{len(chunks)} chunk(s) saved to {output_dir}")
    return chunks


def update_parse_pdf(input_pdf_path, chunks):
    """Update the chunks list in parse_pdf.py for the matching document."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    parse_pdf_path = os.path.join(script_dir, "parse_pdf.py")

    if not os.path.exists(parse_pdf_path):
        print("  parse_pdf.py not found, skipping update.")
        return

    with open(parse_pdf_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Normalize the input path to be relative to script dir
    abs_input = os.path.abspath(input_pdf_path)
    rel_path = os.path.relpath(abs_input, script_dir)

    # Find the entry that has this path
    if rel_path not in content:
        print(f"  Could not find '{rel_path}' in parse_pdf.py, skipping update.")
        return

    # Build new chunks Python code
    chunk_lines = []
    for c in chunks:
        chunk_lines.append(
            f'                    {{"docId": "", "start_page": {c["start_page"]}, "end_page": {c["end_page"]}}}'
        )
    new_chunks_str = ",\n".join(chunk_lines)
    new_block = f'"chunks": [\n{new_chunks_str},\n                ],'

    # Replace existing chunks block or add one
    # Pattern: find the dict entry containing the path, replace/add chunks
    # Look for the block: {"path": "<rel_path>", ...}
    # We need to find the enclosing dict for this path entry

    # Strategy: find line with the path, then find the enclosing { ... } block
    lines = content.split("\n")
    path_line_idx = None
    for i, line in enumerate(lines):
        if rel_path in line and '"path"' in line:
            path_line_idx = i
            break

    if path_line_idx is None:
        print(f"  Could not find path line in parse_pdf.py, skipping update.")
        return

    # Check if chunks already exist — find the block boundaries
    # Look backwards for the opening of this dict (line with doc name key)
    block_start = path_line_idx
    for i in range(path_line_idx, -1, -1):
        stripped = lines[i].strip()
        if stripped.startswith('"') and '{' in stripped:
            block_start = i
            break

    # Find closing of this dict block
    brace_depth = 0
    block_end = path_line_idx
    for i in range(block_start, len(lines)):
        brace_depth += lines[i].count('{') - lines[i].count('}')
        if brace_depth <= 0:
            block_end = i
            break

    # Extract the doc name from block_start line
    old_block = "\n".join(lines[block_start:block_end + 1])

    # Get indentation from the path line
    indent = "                "

    # Build new block
    doc_name_match = re.match(r'(\s+"[^"]+":\s*\{)', lines[block_start])
    if not doc_name_match:
        print("  Could not parse block structure, skipping update.")
        return

    new_block_lines = [
        lines[block_start],  # doc name + {
        f'{indent}"path": "{rel_path}",',
        f'{indent}"chunks": [',
    ]
    for c in chunks:
        new_block_lines.append(
            f'                    {{"docId": "", "start_page": {c["start_page"]}, "end_page": {c["end_page"]}}},'
        )
    new_block_lines.append(f'{indent}],')  # close chunks
    new_block_lines.append('            },')  # close doc dict

    new_block = "\n".join(new_block_lines)
    new_content = content.replace(old_block, new_block)

    with open(parse_pdf_path, "w", encoding="utf-8") as f:
        f.write(new_content)

    print(f"  Updated parse_pdf.py with {len(chunks)} chunk(s) (docIds left empty for you to fill in).")


def main():
    parser = argparse.ArgumentParser(description="Split a PDF into chunks under a size limit.")
    parser.add_argument("input", help="Path to the PDF to split")
    parser.add_argument("-o", "--output-dir", default=None,
                        help="Output directory (default: <input_dir>/splits/)")
    parser.add_argument("--max-mb", type=float, default=4.5,
                        help="Max chunk size in MB (default: 4.5)")
    parser.add_argument("--pages", type=str, default=None,
                        help="Page range to split, e.g. '1-20' (1-indexed, inclusive)")
    parser.add_argument("--chunk-pages", type=int, default=None,
                        help="Force exact pages per chunk (overrides size-based calculation)")
    parser.add_argument("--update", action="store_true",
                        help="Auto-update chunks in parse_pdf.py after splitting")

    args = parser.parse_args()

    output_dir = args.output_dir
    if not output_dir:
        output_dir = os.path.join(os.path.dirname(args.input), "splits")

    page_range = None
    if args.pages:
        parts = args.pages.split("-")
        page_range = (int(parts[0]), int(parts[1]))

    chunks = split_pdf(args.input, output_dir, max_size_mb=args.max_mb, page_range=page_range,
                        force_chunk_pages=args.chunk_pages)

    if args.update:
        update_parse_pdf(args.input, chunks)


if __name__ == "__main__":
    main()
