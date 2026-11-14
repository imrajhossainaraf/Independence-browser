# Operations Guide: Independent Search Engine

## 1. News Ingestion Poller

The news poller runs as an operator CLI command on a schedule:
```bash
search-engine news poll --mode real
```
In automated environments (such as Windows Task Scheduler), `scripts/news-poll.ps1` wraps the execution with timestamped logging and parameter propagation.

### Concurrency & Locking
To avoid overlapping polling runs:
- `ProcessLock` creates an exclusive lock file at `data/news_poll.lock`.
- On Windows, byte-range locking (`msvcrt.locking`) is enforced at the kernel level.
- Stale locks (from killed or crashed processes) are detected and recovered automatically by inspecting the process ID using `psutil.pid_exists()`.
- If an active instance is running, concurrent invocations exit immediately with status `refused_lock_held`.

### Windows Task Scheduler Setup
To schedule automated news polling every 15-30 minutes:
1. Open **Task Scheduler** (`taskschd.msc`).
2. Click **Create Task** (not Basic Task) and configure:
   - **General**:
     - Name: `IndependentSearchEngine-NewsPoll`
     - Description: `Periodic news feed ingestion and headline clustering for Independent Search Engine`
     - Run only when user is logged on (or run whether user is logged on if configured with service account)
   - **Triggers**:
     - Daily, Repeat task every: `15 minutes` (or `30 minutes`) for a duration of: `Indefinitely`
     - Stop task if it runs longer than: `10 minutes`
   - **Actions**:
     - Action: `Start a program`
     - Program/script: `powershell.exe`
     - Add arguments: `-NoProfile -NonInteractive -ExecutionPolicy Bypass -File scripts\news-poll.ps1`
     - Start in: `<path_to_repository_root>` (e.g., `C:\Users\Aru\Desktop\Browser`)
   - **Settings**:
     - If the task is already running, then the following rule applies: `Do not start a new instance`

---

## 2. Source Configuration

Approved sources are configured in `data/news_sources.txt`:
```text
# Format: feed_url, display_name, category, poll_interval_minutes, images_allowed (yes/no)
https://example.com/rss.xml, Example News, General, 30, no
```
- **Poll Interval**: Minimum 15 minutes, default 30 minutes.
- **Politeness**: Evaluated against robots.txt and conditional requests (ETag and Last-Modified).
- **Retention**: Items older than 14 days are deactivated. Active items are capped at 500, pruning oldest first.
- **Article Storage**: Home page news items never store article bodies, only verified summaries.
