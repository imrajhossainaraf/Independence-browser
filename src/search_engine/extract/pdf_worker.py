"""Subprocess worker for isolated PDF text extraction using pypdf."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError


def main() -> None:
    if len(sys.argv) < 2:
        print(json.dumps({"status": "error", "warnings": ["missing_pdf_path"]}))
        sys.exit(1)

    pdf_path = Path(sys.argv[1])
    max_pages = int(sys.argv[2]) if len(sys.argv) > 2 else 50
    max_text_bytes = int(sys.argv[3]) if len(sys.argv) > 3 else 500 * 1024

    if not pdf_path.exists():
        print(json.dumps({"status": "error", "warnings": ["file_not_found"]}))
        sys.exit(1)

    warnings: list[str] = []

    try:
        reader = PdfReader(str(pdf_path))
    except (PdfReadError, Exception) as exc:  # noqa: BLE001
        print(json.dumps({"status": "corrupt", "warnings": [f"pdf_read_error: {exc}"]}))
        sys.exit(0)

    if reader.is_encrypted:
        # Encrypted PDF
        title = ""
        try:
            if reader.metadata and reader.metadata.title:
                title = str(reader.metadata.title).strip()
        except Exception:  # noqa: BLE001, S110
            pass
        print(
            json.dumps(
                {
                    "status": "encrypted",
                    "title": title,
                    "text": "",
                    "page_count": 0,
                    "warnings": ["encrypted_pdf"],
                }
            )
        )
        sys.exit(0)

    total_pages = len(reader.pages)
    if total_pages > max_pages:
        warnings.append("pdf_page_limit_exceeded")

    title = ""
    created_at = None
    try:
        meta = reader.metadata
        if meta:
            if meta.title:
                title = str(meta.title).strip()
            if meta.creation_date:
                created_at = str(meta.creation_date)
    except Exception:  # noqa: BLE001, S110
        pass

    text_parts: list[str] = []
    current_bytes = 0
    pages_to_extract = min(total_pages, max_pages)

    for i in range(pages_to_extract):
        try:
            page_text = reader.pages[i].extract_text() or ""
            if page_text:
                encoded = page_text.encode("utf-8")
                if current_bytes + len(encoded) > max_text_bytes:
                    allowed = max_text_bytes - current_bytes
                    text_parts.append(encoded[:allowed].decode("utf-8", errors="ignore"))
                    warnings.append("pdf_text_truncated")
                    break
                text_parts.append(page_text)
                current_bytes += len(encoded)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"page_{i}_extract_error: {exc}")

    extracted_text = "\n\n".join(text_parts).strip()
    if not extracted_text:
        status = "scanned"
        warnings.append("scanned_pdf")
    else:
        status = "ok"

    result = {
        "status": status,
        "title": title,
        "text": extracted_text,
        "page_count": total_pages,
        "created_at": created_at,
        "warnings": warnings,
    }
    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
