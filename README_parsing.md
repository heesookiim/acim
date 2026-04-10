# PDF Parsing Guide

## Overview

Two scripts handle PDF parsing for the ACIM search engine:

- **`split_pdf.py`** — Splits large PDFs into chunks under 4.5 MB (PDF.ai upload limit)
- **`parse_pdf.py`** — Parses PDFs via the PDF.ai API and saves structured JSON to `parse_data.json`

## Prerequisites

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Set your PDF.ai API key in `.env`:
   ```
   PDF_AI_API_KEY=your_key_here
   ```

## Workflow

### 1. Check if splitting is needed

PDF.ai's file upload limit is **4.5 MB**. If your PDF is under that, skip to step 3.

### 2. Split large PDFs

```bash
# Auto-split and update parse_pdf.py with chunk page ranges
python split_pdf.py <pdf_path> -o <output_dir> --update
```

**Example:**
```bash
python split_pdf.py version/park/교과서_박영수.pdf -o version/park/splits --update
```

This will:
- Split the PDF into chunks under 4.5 MB
- Save chunks to `version/park/splits/`
- Auto-update `parse_pdf.py`'s `VERSIONS` dict with `start_page`/`end_page` (docIds left empty)

**Options:**
| Flag | Description |
|------|-------------|
| `-o DIR` | Output directory (default: `<pdf_dir>/splits/`) |
| `--max-mb N` | Max chunk size in MB (default: 4.5) |
| `--pages 1-20` | Only split a page range (1-indexed, inclusive) |
| `--chunk-pages N` | Force exact pages per chunk |
| `--update` | Auto-update `parse_pdf.py` with chunk info |

### 3. Upload to PDF.ai

- **Small PDFs (< 4.5 MB):** Upload the original file on [pdf.ai](https://pdf.ai)
- **Split PDFs:** Upload each chunk file from the splits directory

Copy the `docId` returned for each upload.

### 4. Add docIds to `parse_pdf.py`

Edit the `VERSIONS` dict in `parse_pdf.py`:

- **Single file:** Set `"docId": "your_doc_id"`
- **Chunked file:** Set `"docId"` in each chunk entry

### 5. Run the parser

```bash
# Parse a specific document
python parse_pdf.py 박영수 교과서

# Parse everything with a docId set
python parse_pdf.py

# List available targets
python parse_pdf.py --list
```

Output is saved to `parse_data.json`, which `app.py` loads at startup.

### 6. Verify

```bash
python app.py
# Open http://127.0.0.1:5001
```
