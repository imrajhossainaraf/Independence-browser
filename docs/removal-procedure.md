# Content Removal and Takedown Procedure
> **[DRAFT - PENDING OPERATOR LEGAL REVIEW]**
> *Runbook for operators handling takedown requests, URL removals, and domain exclusions.*

---

## 1. Overview

Content owners, site administrators, or individuals may request the removal of specific URLs, entire domains, news items, or images from the search engine.

Operators must process valid requests promptly using the built-in CLI commands described below.

---

## 2. Removing Specific Web Pages or Entire Domains

### Remove a Single URL
To remove a single web page from the search results and deindex it immediately:
```bash
search-engine remove --url "https://example.com/unwanted-page" --purge-raw --reason "rightsholder_request"
```
**Effects**:
- Document status in `documents` is updated to `removed`.
- Inverted index rows in `doc_fts` and `doc_fts_tri` are permanently deleted.
- URL status in `urls` is marked `blocked`.
- Future recrawls will skip this URL immediately.
- If `--purge-raw` is specified, the cached raw gzip file on disk is deleted if not referenced by other active pages.

### Remove an Entire Domain
To remove all documents originating from a registered domain:
```bash
search-engine remove --domain "example.com" --purge-raw --reason "domain_exclusion"
```
**Effects**:
- All matching documents across subdomains are deindexed and marked `removed`.
- All matching URLs in `urls` are marked `blocked`.
- All unreferenced raw files are removed from storage.

---

## 3. Persistent Blocking (Preventing Future Discovery)

To guarantee that a host, domain, or URL path prefix can never be queued or fetched in any future crawl:
```bash
# Block an entire domain
search-engine block domain example.com --reason "opt_out"

# Block a specific hostname
search-engine block host bad.example.com --reason "unwanted"

# Block a URL path prefix
search-engine block prefix "https://example.com/private/" --reason "confidential_prefix"
```

To list all active operator blocks:
```bash
search-engine blocks
```

To unblock a target if a request is rescinded:
```bash
search-engine unblock domain example.com
```

---

## 4. News Feed Source & Item Takedown

### Disable an Entire News Feed
To stop polling a feed and remove all of its stories from home and search:
```bash
search-engine news disable-source "https://example.com/rss.xml"
```

### Remove a Specific News Item
To remove an individual news article summary:
```bash
search-engine news remove-item "news_item_id" --reason "publisher_request"
```

---

## 5. Image Thumbnail Takedown

To remove an image thumbnail and physically delete its files from disk:
```bash
search-engine images takedown "img_123456" --reason "dmca_notice"
```
**Effects**:
- Image status in `images` table is marked `removed`.
- Re-encoded WebP thumbnail files (160px and 480px) are unlinked and purged from disk immediately.
- Any subsequent requests to `/api/v1/images/{id}` return HTTP 404 Not Found.

---

## 6. Verification and Audit

After executing any removal:
1. Verify the item no longer appears in search queries:
   ```bash
   search-engine search "query that previously matched removed content"
   ```
2. Verify index consistency:
   ```bash
   search-engine status
   ```
3. Audit the event trail:
   The `events` table records an audit trail with timestamp, operator subject, and reason code for every removal action.
