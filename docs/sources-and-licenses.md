# Sources and Licenses: Independent Search Engine
> **[DRAFT - PENDING OPERATOR LEGAL AND SOURCE REVIEW]**
> *This document details the data sources, intellectual property boundaries, and license compliance rules governing the search engine.*

---

## 1. Web Crawl Corpus

The document search index is built exclusively from websites listed in the human-approved `data/seeds.txt` allowlist:
- **Allowlist Mode**: Only registered domains explicitly listed in seeds (or approved feeds) are queued. External links discovered during crawls are recorded for graph reference but never queued or fetched.
- **Fair Use & Transformative Retrieval**: The engine stores extracted text for full-text indexing (`doc_fts`) and extracts short, safe snippets (up to 240 characters) to assist users in locating source documents.
- **Direct Linking**: All search results prominently display the original canonical URL and registered domain so users visit the original publishers.

---

## 2. News Feeds & Syndication

News data is ingested exclusively from open RSS and Atom feeds specified in `data/news_sources.txt`:
- **Headlines & Summaries Only**: The ingestion engine stores **only** feed-provided headlines and short plain-text summaries (capped at 300 characters). Full article bodies are **never** crawled, scraped, or stored for news items.
- **Publisher Attribution**: Every news cluster and story item prominently displays the source name, publication timestamp, and direct hyperlink to the original publisher article.
- **Freshness Window**: News items are retained for a maximum of 14 days and automatically deactivated thereafter.

---

## 3. Entity Knowledge Store (Release B)

When enabled, entity knowledge panels are populated exclusively from approved open knowledge repositories:
- **Wikipedia**:
  - Text extracts and entity summaries are sourced from Wikimedia APIs under the **Creative Commons Attribution-ShareAlike 4.0 International (CC BY-SA 4.0)** license.
  - Every entity record stores the exact source URL, revision ID, retrieval date, and license indicator.
- **Wikidata**:
  - Structured facts (up to 8 key-value pairs per entity) are sourced under the **Creative Commons CC0 1.0 Universal Public Domain Dedication**.
- **Attribution Display**: All rendered entity cards must display stored license and attribution lines.

---

## 4. Image Pipeline & Thumbnails

- **Approved Sources Only**: Images are accepted exclusively from allowlisted hosts and approved feeds where the publisher has authorized image syndication (`images_allowed = yes`).
- **Thumbnail Re-Encoding**: All stored images are re-encoded locally to WebP thumbnails at fixed widths (160px and 480px) with all EXIF and camera metadata stripped.
- **Zero Hotlinking**: The web client serves thumbnails exclusively from local API endpoints (`/api/v1/images/{id}`) and never hotlinks directly to remote host servers.
- **Takedown Compliance**: Rightsholders can request immediate takedown of any image via the operator CLI (`search-engine images takedown <id>`), which purges physical files from disk.
