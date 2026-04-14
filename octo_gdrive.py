"""
octo_gdrive.py — Octodamus Full Backup to Google Drive

Zips all essential Octodamus files and uploads to Google Drive as a
dated snapshot. Run every 4 hours via Task Scheduler.

Modes:
  --mode auth     One-time OAuth flow (opens browser)
  --mode backup   Create zip and upload (default)
  --mode restore  List available backups
  --mode status   Show Drive folder contents

Setup:
  1. Place gdrive_credentials.json in C:/Users/walli/octodamus/
  2. Run: python octo_gdrive.py --mode auth
  3. Task Scheduler runs --mode backup every 4 hours
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

INCLUDE_EXTENSIONS = {".py", ".json", ".md", ".ps1", ".xml", ".yaml", ".txt", ".html"}

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
    "nohup.out", "octo_startup.log", "botcoin_withdraw.log",
    # large binary / generated
    "octo_logo_b64.txt",
}

EXCLUDE_DIRS = {
    "__pycache__", ".git", "logs", "charts", "reports", "snapshots",
    "website_files", "__MACOSX", "OCTODAMUS MASTER DATA FILES_All Chats",
    "journals",
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
            # Extension filter
            if fpath.suffix.lower() not in INCLUDE_EXTENSIONS:
                # Allow extensionless files we explicitly want
                if fname not in {".octo_secrets"}:
                    continue
            # Name exclusion
            if fname in EXCLUDE_NAMES:
                continue
            # Skip .bak files
            if ".bak" in fname or fname.endswith(".bak"):
                continue
            # Skip preview/strategy PNGs (already filtered by extension but just in case)
            files.append(fpath)
    return sorted(files)


def _build_zip() -> tuple[bytes, int, list[str]]:
    """Create an in-memory zip of all backup files. Returns (bytes, count, names)."""
    files = _collect_files()
    buf = io.BytesIO()
    names = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for fpath in files:
            arcname = str(fpath.relative_to(BASE_DIR))
            try:
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
            creds.refresh(Request())
        else:
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

    # Write manifest
    manifest = {
        "backup_time": now.isoformat(),
        "zip_name": zip_name,
        "file_count": file_count,
        "size_mb": round(size_mb, 2),
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
    parser.add_argument("--mode", choices=["backup", "restore", "auth", "status"],
                        default="backup")
    args = parser.parse_args()

    if args.mode == "auth":
        print("[GDrive] Starting OAuth flow...")
        _get_service()
        print("[GDrive] Auth complete. Token saved.")
    elif args.mode == "backup":
        backup()
    elif args.mode == "restore":
        list_restores()
    elif args.mode == "status":
        status()
