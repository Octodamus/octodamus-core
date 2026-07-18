"""
octo_gdrive.py — Octodamus Full Backup to Google Drive

Zips all essential Octodamus files and uploads to Google Drive as a
dated snapshot. Run every 4 hours via Task Scheduler.

Full disaster-recovery snapshot of the WHOLE Octodamus ecosystem — Octodamus, OctoBoto,
Agent_Ben, and every sub-agent under .agents/ — including their live SQLite memory DBs,
event journals, per-agent state/memory, and secrets. Restorable on a fresh machine.

Modes:
  --mode auth     One-time OAuth flow (opens browser)
  --mode backup   Create zip and upload (default)
  --mode restore  List available backups
  --mode pull     Download + extract the latest backup to a folder (disaster recovery)
  --mode status   Show Drive folder contents

Setup:
  1. Place gdrive_credentials.json in C:/Users/walli/octodamus/
  2. Run: python octo_gdrive.py --mode auth
  3. Task Scheduler runs --mode backup every 4 hours

Restore on a new machine:
  python octo_gdrive.py --mode auth              # sign in (needs gdrive_credentials.json)
  python octo_gdrive.py --mode pull --dest DIR   # download + unzip latest full snapshot
  then: pip install -r requirements.txt, `npm install` in skill dirs, run octo_startup.ps1
"""

import io
import json
import os
import sys
import argparse
import zipfile
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR    = Path(__file__).parent.resolve()
CREDS_FILE  = BASE_DIR / "gdrive_credentials.json"
TOKEN_FILE  = BASE_DIR / "gdrive_token.json"
FOLDER_NAME = "Octodamus-Backup"
KEEP_BACKUPS = 7   # number of zips to retain on Drive

# ── What to include ───────────────────────────────────────────────────────────

# Code + config + ALL persistent state. .db = SQLite memory/session DBs (calibration,
# skill log, Ben lessons, session FTS); .jsonl = event journals (ACP history);
# .csv/.sol/.pdf = data-product samples, contract source, product assets.
INCLUDE_EXTENSIONS = {".py", ".json", ".md", ".ps1", ".xml", ".yaml", ".yml", ".txt",
                      ".html", ".db", ".jsonl", ".csv", ".sol", ".pdf"}

# Extensionless files we explicitly want (secrets + env config for a runnable restore).
INCLUDE_NAMES = {".octo_secrets", ".env"}

EXCLUDE_NAMES = {
    # old bak files
    "bitwarden.py.bak", "bitwarden.py.bak_six",
    "octo_api_server.py.bak", "octo_api_server.py.bak4",
    "octo_api_server.py.bak_pre_acp", "octo_api_server.py.bak_pre_btcfix",
    "octo_api_server_backup.py",
    "telegram_bot.py.bak", "telegram_bot.py.bak3", "telegram_bot.py.bak_six",
    "octodamus_runner.py.bak", "octodamus_runner.py.bak2",
    "octodamus_runner.py.bak3", "octodamus_runner.py.bak_indent",
    "octodamus_runner.py.bak_pre_six", "octodamus_runner.py.bak_pre_voice",
    "octodamus_runner.py.bak_pre_voice2", "octodamus_runner.py.bak_six",
    "octodamus_runner.py.final_bak",
    "octo_gecko.py.bak",
    "octo_geo.py.bak_fix2",
    "octo_predict.py.bak_filter", "octo_predict.py.bak_fix2",
    "octo_predict.py.bak_parser", "octo_predict.py.bak_terms",
    # runtime / log files
    "nohup.out", "octo_startup.log",
    # large binary / generated
    "octo_logo_b64.txt",
}

EXCLUDE_DIRS = {
    "__pycache__", ".git", "logs", "charts", "reports", "snapshots",
    "website_files", "__MACOSX", "OCTODAMUS MASTER DATA FILES_All Chats",
    "journals",
    # Reinstallable dependencies & regenerable caches — not part of the restore payload.
    # (node_modules alone was silently bloating every backup with thousands of dep files.)
    "node_modules", ".venv", "venv", "firecrawl_cache", "firecrawl_cache",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".next", "dist", "build",
}

# Sensitive files — backed up but excluded from zip visible content
# (they're included; note is here for awareness)
SENSITIVE = {"gdrive_credentials.json", "gdrive_token.json", "octo_extra_secrets.json",
             "api_keys.json", "internal_api_key.txt"}


def _collect_files() -> list[Path]:
    """Walk BASE_DIR and collect all files worth backing up."""
    files = []
    for root, dirs, filenames in os.walk(BASE_DIR):
        root_path = Path(root)
        # Prune excluded directories
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]

        for fname in filenames:
            fpath = root_path / fname
            # Extension filter (or an explicitly-wanted extensionless file)
            if fpath.suffix.lower() not in INCLUDE_EXTENSIONS and fname not in INCLUDE_NAMES:
                continue
            # Name exclusion
            if fname in EXCLUDE_NAMES:
                continue
            # Skip .bak / editor-backup variants (bak, bak2, bak_six, final_bak, ...)
            if ".bak" in fname.lower() or ".dead-" in fname.lower():
                continue
            files.append(fpath)
    return sorted(files)


def _sqlite_snapshot(src: Path) -> bytes | None:
    """Consistent snapshot of a possibly-live SQLite DB via the online backup API.

    A raw file copy of a DB that an agent is mid-write on can be torn or locked (then
    skipped -> silently absent from the backup). The backup API yields a committed,
    self-consistent copy. Returns snapshot bytes, or None to fall back to a raw copy.
    """
    import sqlite3
    import tempfile
    tmp = Path(tempfile.gettempdir()) / f"_octobak_{src.name}"
    src_con = dst_con = None
    try:
        src_con = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=8)
        dst_con = sqlite3.connect(str(tmp))
        with dst_con:
            src_con.backup(dst_con)
        return tmp.read_bytes()
    except Exception as e:
        print(f"[GDrive] SQLite snapshot failed for {src.name}: {e} — using raw copy")
        return None
    finally:
        for c in (src_con, dst_con):
            try:
                if c: c.close()
            except Exception:
                pass
        tmp.unlink(missing_ok=True)


def _build_zip() -> tuple[bytes, int, list[str]]:
    """Create an in-memory zip of all backup files. Returns (bytes, count, names)."""
    files = _collect_files()
    buf = io.BytesIO()
    names = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for fpath in files:
            arcname = str(fpath.relative_to(BASE_DIR))
            try:
                if fpath.suffix.lower() == ".db":
                    snap = _sqlite_snapshot(fpath)
                    if snap is not None:
                        zf.writestr(arcname, snap)  # consistent point-in-time copy
                    else:
                        zf.write(fpath, arcname)     # best-effort raw fallback
                else:
                    zf.write(fpath, arcname)
                names.append(arcname)
            except (PermissionError, OSError):
                pass  # skip locked files
    return buf.getvalue(), len(names), names


# ── Google Drive ──────────────────────────────────────────────────────────────

def _get_service():
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        print("[GDrive] Missing deps: pip install google-api-python-client google-auth-oauthlib")
        sys.exit(1)

    SCOPES = ["https://www.googleapis.com/auth/drive.file"]
    creds = None

    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                # Dead refresh token (invalid_grant) -- don't crash the 4h backup silently.
                # Fall through to interactive re-auth (needs a browser, run manually once).
                print(f"[GDrive] Token refresh failed: {e}. Re-authorization required -- "
                      f"run `python octo_gdrive.py` interactively (opens a browser) to sign in again.")
                creds = None
        if not (creds and creds.valid):
            if not CREDS_FILE.exists():
                print(f"[GDrive] credentials.json not found at {CREDS_FILE}")
                sys.exit(1)
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json())

    return build("drive", "v3", credentials=creds)


def _get_or_create_folder(service, name: str) -> str:
    results = service.files().list(
        q=f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false",
        fields="files(id, name)"
    ).execute()
    files = results.get("files", [])
    if files:
        return files[0]["id"]
    folder = service.files().create(
        body={"name": name, "mimeType": "application/vnd.google-apps.folder"},
        fields="id"
    ).execute()
    print(f"[GDrive] Created folder: {name}")
    return folder["id"]


def _prune_old_backups(service, folder_id: str, keep: int):
    """Delete oldest zip backups beyond the keep limit."""
    results = service.files().list(
        q=f"'{folder_id}' in parents and name contains 'octodamus_backup_' and trashed=false",
        fields="files(id, name, createdTime)",
        orderBy="createdTime"
    ).execute()
    zips = results.get("files", [])
    if len(zips) > keep:
        to_delete = zips[:len(zips) - keep]
        for f in to_delete:
            service.files().delete(fileId=f["id"]).execute()
            print(f"[GDrive] Pruned old backup: {f['name']}")


# ── Main operations ───────────────────────────────────────────────────────────

def backup():
    now = datetime.now(timezone.utc)
    zip_name = f"octodamus_backup_{now.strftime('%Y%m%d_%H%M')}.zip"

    print(f"[GDrive] Building zip...")
    zip_bytes, file_count, file_names = _build_zip()
    size_mb = len(zip_bytes) / 1_048_576
    print(f"[GDrive] Zipped {file_count} files — {size_mb:.1f} MB")

    print(f"[GDrive] Uploading {zip_name}...")
    service = _get_service()
    folder_id = _get_or_create_folder(service, FOLDER_NAME)

    from googleapiclient.http import MediaIoBaseUpload
    media = MediaIoBaseUpload(io.BytesIO(zip_bytes), mimetype="application/zip", resumable=True)
    service.files().create(
        body={"name": zip_name, "parents": [folder_id]},
        media_body=media,
        fields="id"
    ).execute()
    print(f"[GDrive] Uploaded: {zip_name}")

    # Also update a rolling latest.zip for easy access
    existing_latest = service.files().list(
        q=f"'{folder_id}' in parents and name='octodamus_latest.zip' and trashed=false",
        fields="files(id)"
    ).execute().get("files", [])
    media2 = MediaIoBaseUpload(io.BytesIO(zip_bytes), mimetype="application/zip", resumable=True)
    if existing_latest:
        service.files().update(fileId=existing_latest[0]["id"], media_body=media2).execute()
    else:
        service.files().create(
            body={"name": "octodamus_latest.zip", "parents": [folder_id]},
            media_body=media2, fields="id"
        ).execute()
    print(f"[GDrive] Updated: octodamus_latest.zip")

    _prune_old_backups(service, folder_id, KEEP_BACKUPS)

    # Write manifest (with an ecosystem summary to verify completeness at a glance)
    agents_dir = BASE_DIR / ".agents"
    agents = sorted(d.name for d in agents_dir.iterdir()
                    if d.is_dir() and not d.name.startswith(".") and d.name != "skills") \
             if agents_dir.exists() else []
    manifest = {
        "backup_time": now.isoformat(),
        "zip_name": zip_name,
        "file_count": file_count,
        "size_mb": round(size_mb, 2),
        "ecosystem": {
            "agents": agents,
            "databases": [n for n in file_names if n.endswith(".db")],
            "event_journals": [n for n in file_names if n.endswith(".jsonl")],
            "core_memories": [n for n in file_names
                              if n.endswith("_core.md") and "memory" in n.replace("\\", "/")],
            "secrets_included": [n for n in file_names
                                 if n in (".octo_secrets", ".env") or "secret" in n.lower()],
        },
        "files": file_names,
    }
    mf_bytes = json.dumps(manifest, indent=2).encode("utf-8")
    from googleapiclient.http import MediaIoBaseUpload as MIU
    existing_mf = service.files().list(
        q=f"'{folder_id}' in parents and name='backup_manifest.json' and trashed=false",
        fields="files(id)"
    ).execute().get("files", [])
    mf_media = MIU(io.BytesIO(mf_bytes), mimetype="application/json", resumable=False)
    if existing_mf:
        service.files().update(fileId=existing_mf[0]["id"], media_body=mf_media).execute()
    else:
        service.files().create(
            body={"name": "backup_manifest.json", "parents": [folder_id]},
            media_body=mf_media, fields="id"
        ).execute()

    print(f"[GDrive] Backup complete — {file_count} files, {size_mb:.1f} MB")
    return True


def pull(dest: str | None = None):
    """Disaster recovery: download the latest full snapshot and extract it, so the whole
    ecosystem can be stood up on a fresh machine."""
    dest_dir = Path(dest).resolve() if dest else (BASE_DIR.parent / "octodamus_restore")
    service = _get_service()
    folder_id = _get_or_create_folder(service, FOLDER_NAME)
    found = service.files().list(
        q=f"'{folder_id}' in parents and name='octodamus_latest.zip' and trashed=false",
        fields="files(id, name, size, modifiedTime)"
    ).execute().get("files", [])
    if not found:
        print("[GDrive] No octodamus_latest.zip on Drive — run a backup first.")
        return False
    meta = found[0]
    print(f"[GDrive] Downloading {meta['name']} ({int(meta.get('size',0))/1_048_576:.1f} MB, "
          f"{meta['modifiedTime'][:16]})...")

    from googleapiclient.http import MediaIoBaseDownload
    buf = io.BytesIO()
    dl = MediaIoBaseDownload(buf, service.files().get_media(fileId=meta["id"]))
    done = False
    while not done:
        _, done = dl.next_chunk()

    dest_dir.mkdir(parents=True, exist_ok=True)
    buf.seek(0)
    with zipfile.ZipFile(buf) as zf:
        zf.extractall(dest_dir)
        n = len(zf.namelist())
    print(f"[GDrive] Restored {n} files to {dest_dir}")
    print("[GDrive] Next: pip install -r requirements.txt  |  `npm install` in skill/agent "
          "dirs that need it  |  then run octo_startup.ps1 to bring the ecosystem up.")
    return True


def status():
    service = _get_service()
    folder_id = _get_or_create_folder(service, FOLDER_NAME)
    results = service.files().list(
        q=f"'{folder_id}' in parents and trashed=false",
        fields="files(name, modifiedTime, size)",
        orderBy="modifiedTime desc"
    ).execute()
    files = results.get("files", [])
    print(f"\nOctodamus-Backup on Google Drive ({len(files)} files):")
    for f in files:
        size_mb = int(f.get("size", 0)) / 1_048_576
        print(f"  {f['name']:<45} {f['modifiedTime'][:16]}  {size_mb:.1f} MB")


def list_restores():
    service = _get_service()
    folder_id = _get_or_create_folder(service, FOLDER_NAME)
    results = service.files().list(
        q=f"'{folder_id}' in parents and name contains 'octodamus_backup_' and trashed=false",
        fields="files(id, name, createdTime, size)",
        orderBy="createdTime desc"
    ).execute()
    zips = results.get("files", [])
    print(f"\nAvailable backups ({len(zips)}):")
    for z in zips:
        size_mb = int(z.get("size", 0)) / 1_048_576
        print(f"  {z['name']}  —  {z['createdTime'][:16]}  ({size_mb:.1f} MB)  id:{z['id']}")


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Octodamus Google Drive Full Backup")
    parser.add_argument("--mode", choices=["backup", "restore", "pull", "auth", "status"],
                        default="backup")
    parser.add_argument("--dest", default=None,
                        help="Target folder for --mode pull (default: ../octodamus_restore)")
    args = parser.parse_args()

    if args.mode == "auth":
        print("[GDrive] Starting OAuth flow...")
        _get_service()
        print("[GDrive] Auth complete. Token saved.")
    elif args.mode == "backup":
        backup()
    elif args.mode == "restore":
        list_restores()
    elif args.mode == "pull":
        pull(args.dest)
    elif args.mode == "status":
        status()
