# ACIM Korean Search Engine — Workspace Instructions

## Overview
This workspace implements a Korean-language search engine for ACIM (A Course in Miracles) documents. It includes tools for extracting, processing, and searching text from multiple PDF versions, as well as a Flask web application for user interaction.

## Build & Run
- **Web app (Flask):**
  - Local: `python3 app.py` (development)
  - Production: `gunicorn app:app` (see `Procfile`)
- **Extract text:**
  - `python3 extract_text.py` — Extracts text from PDFs to JSON for search
- **Extract reader data:**
  - `python3 extract_read_data.py` — Builds side-by-side units for reading

## Project Structure
- `app.py` — Flask web server and search logic
- `extract_text.py` — PDF text extraction to JSON
- `extract_read_data.py` — Reader-oriented data extraction (side-by-side, images)
- `requirements.txt` — Python dependencies (Flask, gunicorn, PyMuPDF, etc.)
- `Procfile` — Gunicorn entrypoint for deployment
- `templates/index.html` — Main web UI
- `search_data.json` — Searchable text data (generated)

## Conventions & Notes
- **PDFs and versions:** Paths and versioning are hardcoded in extractors. Update as needed for new versions.
- **Environment variables:** Some scripts require `GEMINI_API_KEY` and optionally `GEMINI_MODEL`.
- **Cache:** Extractors use `.cache/` for intermediate files.
- **Korean text handling:** Special care is taken to handle Hangul line breaks and sentence boundaries.
- **No tests yet:** Add tests if expanding logic or refactoring.

## Potential Pitfalls
- **PDF file paths:** Ensure all referenced PDFs exist at the expected locations.
- **Large files:** Extraction scripts may use significant memory for large PDFs.
- **API keys:** Required for Gemini-based extraction; not needed for basic search.

## See Also
- For numbering conventions, see `/memories/repo/acim-numbering.md` (if present).

---

## Example Prompts
- "How do I run the web app locally?"
- "How do I update the search data after changing a PDF?"
- "What environment variables are needed for extraction?"

---

## Next Steps / Customizations
- Consider adding tests or a `tests/` directory.
- Add agent instructions for extraction scripts if automating data refresh.
- Propose a skill for PDF text extraction best practices.
