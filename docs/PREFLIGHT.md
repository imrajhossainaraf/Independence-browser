# Preflight Checklist & Operational Guide (Step 29)

This document specifies the mandatory preflight approval protocol, safety verifications, and staged rollout procedures required before any real-network crawling occurs, per Decision D13 and Section 10 (Step 29) of `BUILD_PLAN.md`.

---

## 1. Safety Architecture: The Air-Gap Principle

Under Section 5 (Invariants) and Decision D13:
1. `real_network_enabled` defaults to `false`.
2. When `real_network_enabled` is set to `true`, the crawler and fetcher **strictly refuse to start** unless `data/preflight.approved.json` exists.
3. `data/preflight.approved.json` contains a SHA-256 cryptographic digest over the canonical JSON manifest of:
   - `seeds` (`data/seeds.txt`)
   - `news_sources` (`data/news_sources.txt`)
   - `entities` (`data/entities_seed.txt`)
   - `allowlist_mode` (default `true`)
   - `user_agent_token` & `policy_url`
   - `news_images_enabled` (default `false`)
   - `max_pages` (hard ceiling 1,000)
   - `max_depth` (default 3)
   - `max_pages_per_host` (default 200)
   - `max_storage_bytes` (hard ceiling 1,073,741,824 / 1 GiB)
4. Any modification to seeds, news sources, policies, or caps alters the manifest hash and invalidates preflight authorization, immediately halting the crawler until explicitly re-approved.

---

## 2. Preflight Verification Checklist

Before running `search-engine approve-preflight`, the operator must review and confirm each of the following:

- [ ] **Focused Corpus Selection**:
  - 3 to 5 specific topics selected that the operator actually searches for (e.g. official Python documentation, Web standards, trusted open news feeds).
  - Seed list in `data/seeds.txt` contains only explicitly approved, reputable domains.
- [ ] **News Sources & Image Policies**:
  - `data/news_sources.txt` reviewed. Each feed follows `feed_url | name | category | poll_minutes | images_allowed`.
  - News images are set to `no` for Release A.
  - Feeds respect minimum poll interval of 15 minutes (default 30 minutes).
- [ ] **User-Agent & Reachable Policy URL (D14)**:
  - User-agent configured with operator contact token and live policy URL (e.g. `SearchEngineBot/0.1 (+https://example.com/bot-policy; bot@example.com)`).
  - Crawler policy page published and reachable per `docs/crawler-policy.md`.
- [ ] **Robots.txt & Politeness Controls**:
  - Politeness delays verified: minimum 1.0 second per host, honoring `Crawl-delay` up to 30 seconds.
  - Concurrency caps verified: 1 request per host, maximum 4 global concurrency.
- [ ] **Resource Limits & Free Disk**:
  - Hard page ceiling confirmed: maximum 1,000 pages total.
  - Raw storage byte limits enforced: 2 MiB for HTML/text/feeds, 5 MiB for PDF/sitemaps.
  - Minimum free disk checked (requires >= 2 GB available).
- [ ] **Emergency Stop Rehearsal**:
  - Verified that touching `data/STOP` halts running crawls cleanly at the next URL loop.
  - Verified that creating `data/PAUSE` suspends fetch loop until removed.
- [ ] **Takedown & Removal Procedures**:
  - Verified operator takedown commands: `search-engine remove <url>` and `search-engine block <domain>`.
- [ ] **Verified Initial Backup**:
  - Full backup created via `search-engine backup` before beginning real crawling.

---

## 3. How to Authorize Preflight

Once all checklist items above have been completed by the operator, generate the cryptographic authorization manifest:

```powershell
uv run search-engine approve-preflight
```

This writes `data/preflight.approved.json`. Example manifest:
```json
{
  "approved_at": "2026-11-14T16:30:00Z",
  "approved_by": "operator",
  "manifest_hash": "3f8b...9a0c",
  "manifest": {
    "allowlist_mode": true,
    "entities": [],
    "max_depth": 3,
    "max_pages": 1000,
    "max_pages_per_host": 200,
    "max_storage_bytes": 1073741824,
    "news_images_enabled": false,
    "news_sources": [],
    "policy_url": "https://example.com/crawler-policy",
    "seeds": [
      "https://docs.python.org/3/"
    ],
    "user_agent_token": "SearchEngineBot/0.1"
  }
}
```

---

## 4. Staged Rollout Stages

Never run an unbounded crawl. Execute in four controlled stages, taking a backup after each:

### Stage 1: Initial Handshake (10 pages, 1-2 feeds)
- Run:
  ```powershell
  uv run search-engine crawl --mode real --set max_pages=10
  uv run search-engine news poll --mode real
  ```
- Verify:
  - Robots.txt retrieved and cached.
  - Status codes 200 recorded in database.
  - HTML parsed without errors.
  - News items clustered and displayed on home page.
  - Create backup: `uv run search-engine backup --output-dir backups/stage1`

### Stage 2: Small Cluster (100 pages, up to 10 feeds)
- Run:
  ```powershell
  uv run search-engine crawl --mode real --set max_pages=100
  ```
- Verify:
  - Per-host politeness delay observed (>= 1.0s).
  - Zero private or loopback IP requests.
  - Freshness and clustering operational.
  - Create backup: `uv run search-engine backup --output-dir backups/stage2`

### Stage 3: Focused Corpus (300 to 500 pages, all approved feeds)
- Run:
  ```powershell
  uv run search-engine crawl --mode real --set max_pages=500
  ```
- Verify:
  - FTS5 index rebuilds cleanly.
  - Search queries yield relevant snippets and verbatim grounded synopses.
  - Create backup: `uv run search-engine backup --output-dir backups/stage3`

### Stage 4: Production Baseline (Up to 1,000 pages)
- Run:
  ```powershell
  uv run search-engine crawl --mode real --set max_pages=1000
  ```
- Run daily-use verification:
  ```powershell
  powershell -ExecutionPolicy Bypass -File scripts/daily-use.ps1
  ```
- Create final backup: `uv run search-engine backup --output-dir backups/stage4`

---

## 5. Windows Task Scheduler Setup for News Polling

To poll approved feeds every 30 minutes in the background on Windows:

1. Open PowerShell as Administrator (or standard user if running in user context):
   ```powershell
   $Action = New-ScheduledTaskAction -Execute "PowerShell.exe" -Argument "-ExecutionPolicy Bypass -WindowStyle Hidden -File C:\Users\Aru\Desktop\Browser\scripts\news-poll.ps1"
   $Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 30)
   $Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
   Register-ScheduledTask -TaskName "SearchEngineNewsPoller" -Action $Action -Trigger $Trigger -Settings $Settings -Description "Periodic news feed poller for Independent Search Engine"
   ```
2. To test manually:
   ```powershell
   Start-ScheduledTask -TaskName "SearchEngineNewsPoller"
   ```
3. To disable or remove:
   ```powershell
   Disable-ScheduledTask -TaskName "SearchEngineNewsPoller"
   Unregister-ScheduledTask -TaskName "SearchEngineNewsPoller" -Confirm:$false
   ```
