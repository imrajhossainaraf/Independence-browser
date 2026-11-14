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

---

## 3. Backup, Restore & Disaster Recovery (Decision D11)

### Online Backup Mechanism
The engine uses the SQLite online backup API (`sqlite3.Connection.backup()`) to create consistent database snapshots without stopping active services:
- **Zero-Downtime Snapshots**: Safely creates a point-in-time snapshot of SQLite databases running in WAL mode even while concurrent readers or a writer are active.
- **Atomic File Store Copy**: Extracted gzip raw files (`data/raw/ab/cd/<sha256>.gz`) and WebP image thumbnails (`data/images/ab/cd/<sha256>-<size>.webp`) are copied alongside the database snapshot.
- **Cryptographic Manifest**: Every backup generates a versioned `manifest.json` containing SHA-256 digests and byte sizes of the database and all stored files, plus schema and index versions.
- **Live File Copy Forbidden**: Live copying of active SQLite database files is strictly forbidden per Invariant D11 to avoid partial-page corruptions.

### Creating a Backup
To create a snapshot backup:
```bash
search-engine backup
```
Options:
- `--output <dir>` or `-o <dir>`: Custom destination directory (default: `backups/backup_<YYYYMMDD_HHMMSS>`).
- `--db <path>`: Source SQLite database file.
- `--raw-dir <path>`: Source raw file storage directory.
- `--image-dir <path>`: Source image storage directory.
- `--json`: Machine-readable output with full manifest metadata.

Example output:
```text
Backup created successfully at: backups/backup_20261114_194500
  Schema version: 4
  Index version:  1
  Total files:    142
  Total bytes:    1845920
  Manifest:       backups/backup_20261114_194500/manifest.json
```

### Restoring from a Backup
To restore a backup into a new environment or disaster-recovery location:
```bash
search-engine restore --backup-dir backups/backup_20261114_194500 --target-dir data_restored
```

#### Strict Safety & Integrity Gates
The restore process enforces multiple verification layers before declaring success:
1. **Empty Target Enforcement**: The target directory must be newly created or completely empty. If any files or folders exist, restore aborts immediately with `restore_target_not_empty` to prevent accidental overwrites.
2. **Manifest Verification**: Validates `manifest.json` schema and compatibility (`manifest_version: 1`).
3. **Cryptographic Integrity**: Recomputes SHA-256 hashes of the database snapshot and all associated raw/image files. Any bit flip or missing file triggers immediate rejection with `backup_corrupted`.
4. **SQLite PRAGMA Integrity Check**: Executes `PRAGMA integrity_check` on the restored database. It must return `ok`.
5. **FTS5 Index Consistency Check**: Executes `verify_index_consistency()` to guarantee that every active document in `documents` has matching inverted index entries in `doc_fts` and `doc_fts_tri`.

### Disaster Recovery Runbook
In the event of database corruption, disk failure, or environment migration:
1. **Stop active processes**: Terminate any running crawler or server instances.
2. **Identify latest verified backup**: Select the newest backup folder in `backups/`.
3. **Restore to clean directory**:
   ```bash
   search-engine restore -b backups/backup_latest -t data_new
   ```
4. **Switch data pointer**:
   Update `SEARCH_ENGINE_DB_PATH=data_new/index/search.db` and storage paths in `.env` (or rename directory after archiving damaged data).
5. **Verify health offline**:
   ```bash
   search-engine search "test query" --db data_new/index/search.db
   ```
6. **Resume services**: Restart server and scheduled poller tasks.

---

## 4. Offline Operation

The search engine and thin client are designed to operate completely self-hosted with zero internet connection:
- Invariant 10 & 16: Search, home, panels, and images never make outbound network requests.
- To start the server in explicit offline mode:
  ```bash
  search-engine serve --offline
  ```
- In offline mode, network setup is bypassed, and the server binds strictly to `127.0.0.1`.
- Tests enforce socket blocking (`pytest-socket`) to prove full functionality with zero network access.
