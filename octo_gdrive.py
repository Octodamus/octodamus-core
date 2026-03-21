"""
octo_gdrive.py — Octodamus Google Drive Memory Backup
Backs up BRAIN.md, SOUL.md, and octo_calls.json to Google Drive
every 4 hours (run via Task Scheduler).

Setup:
1. Go to console.cloud.google.com
2. Create project: octodamus-memory
3. Enable Google Drive API
4. Create credentials: OAuth 2.0 Desktop App
5. Download as credentials.json to C:/Users/walli/octodamus/gdrive_credentials.json
6. First run: python3 octo_gdrive.py --auth  (opens browser, saves token)
7. Add to Task Scheduler: every 4 hours, --mode backup

Files backed up to Google Drive folder: Octodamus-Memory/
- BRAIN.md
- SOUL.md
- octo_calls.json
- octo_boto_ledger.json  (OctoBoto wallet/positions)
- dashboard_metrics.json (follower count, guide sales)
"""

import json
import os
import sys
import argparse
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR   = Path(__file__).parent.resolve()
CREDS_FILE = BASE_DIR / "gdrive_credentials.json"
TOKEN_FILE = BASE_DIR / "gdrive_token.json"
FOLDER_NAME = "Octodamus-Memory"

BACKUP_FILES = [
    BASE_DIR / "BRAIN.md",
    BASE_DIR / "SOUL.md",
    BASE_DIR / "data" / "octo_calls.json",
    BASE_DIR / "data" / "octo_boto_ledger.json",
    BASE_DIR / "data" / "dashboard_metrics.json",
    BASE_DIR / "OCTO_LAUNCH_PROTOCOL.md",
]

# ── Google Drive client ───────────────────────────────────────────────────────

def _get_service():
    """Build authenticated Google Drive service."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        print("[GDrive] Missing deps. Run: pip install google-api-python-client google-auth-oauthlib --break-system-packages")
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
                print("[GDrive] Download from console.cloud.google.com → APIs & Services → Credentials")
                sys.exit(1)
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)

        TOKEN_FILE.write_text(creds.to_json())
        print(f"[GDrive] Token saved to {TOKEN_FILE}")

    return build("drive", "v3", credentials=creds)


def _get_or_create_folder(service, name: str) -> str:
    """Get or create a folder in Google Drive root. Returns folder ID."""
    results = service.files().list(
        q=f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false",
        fields="files(id, name)"
    ).execute()

    files = results.get("files", [])
    if files:
        return files[0]["id"]

    folder = service.files().create(
        body={
            "name": name,
            "mimeType": "application/vnd.google-apps.folder"
        },
        fields="id"
    ).execute()
    print(f"[GDrive] Created folder: {name} ({folder['id']})")
    return folder["id"]


def _upload_file(service, local_path: Path, folder_id: str) -> dict:
    """Upload or update a file in the given folder."""
    from googleapiclient.http import MediaFileUpload
    import mimetypes

    if not local_path.exists():
        print(f"[GDrive] Skipping {local_path.name} — not found")
        return {}

    mime_type = mimetypes.guess_type(str(local_path))[0] or "text/plain"

    # Check if file already exists in folder
    results = service.files().list(
        q=f"name='{local_path.name}' and '{folder_id}' in parents and trashed=false",
        fields="files(id, name)"
    ).execute()

    media = MediaFileUpload(str(local_path), mimetype=mime_type, resumable=False)

    existing = results.get("files", [])
    if existing:
        # Update existing file
        file = service.files().update(
            fileId=existing[0]["id"],
            media_body=media,
        ).execute()
        print(f"[GDrive] Updated: {local_path.name}")
    else:
        # Create new file
        file = service.files().create(
            body={
                "name": local_path.name,
                "parents": [folder_id],
            },
            media_body=media,
            fields="id, name"
        ).execute()
        print(f"[GDrive] Uploaded: {local_path.name}")

    return file


def _write_backup_manifest(service, folder_id: str, results: list):
    """Write a backup manifest file with timestamp and file list."""
    import tempfile

    now = datetime.now(timezone.utc).isoformat()
    manifest = {
        "backup_time": now,
        "files": results,
        "agent": "Octodamus (@octodamusai)",
        "version": "1.0"
    }

    tmp = Path(tempfile.mktemp(suffix=".json"))
    tmp.write_text(json.dumps(manifest, indent=2))

    from googleapiclient.http import MediaFileUpload
    existing = service.files().list(
        q=f"name='backup_manifest.json' and '{folder_id}' in parents and trashed=false",
        fields="files(id)"
    ).execute().get("files", [])

    media = MediaFileUpload(str(tmp), mimetype="application/json")
    if existing:
        service.files().update(fileId=existing[0]["id"], media_body=media).execute()
    else:
        service.files().create(
            body={"name": "backup_manifest.json", "parents": [folder_id]},
            media_body=media, fields="id"
        ).execute()

    tmp.unlink()
    print(f"[GDrive] Manifest updated: {now}")


# ── Restore ───────────────────────────────────────────────────────────────────

def restore_latest(dry_run: bool = False):
    """Download latest backups from Google Drive to local octodamus dir."""
    print("[GDrive] Restoring from Google Drive...")
    service = _get_service()
    folder_id = _get_or_create_folder(service, FOLDER_NAME)

    results = service.files().list(
        q=f"'{folder_id}' in parents and trashed=false",
        fields="files(id, name, modifiedTime)"
    ).execute()

    files = results.get("files", [])
    if not files:
        print("[GDrive] No files found in Octodamus-Memory folder")
        return

    for f in files:
        if f["name"] == "backup_manifest.json":
            continue

        target = BASE_DIR / f["name"]
        # Some files are in data/ subdirectory
        if f["name"] in ["octo_calls.json", "octo_boto_ledger.json", "dashboard_metrics.json"]:
            target = BASE_DIR / "data" / f["name"]

        if dry_run:
            print(f"[GDrive] Would restore: {f['name']} → {target}")
            continue

        from googleapiclient.http import MediaIoBaseDownload
        import io
        request = service.files().get_media(fileId=f["id"])
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(fh.getvalue())
        print(f"[GDrive] Restored: {f['name']}")

    print("[GDrive] Restore complete")


# ── Backup ────────────────────────────────────────────────────────────────────

def backup():
    """Backup all memory files to Google Drive."""
    print(f"[GDrive] Starting backup → {FOLDER_NAME}/")

    try:
        service = _get_service()
        folder_id = _get_or_create_folder(service, FOLDER_NAME)

        results = []
        for path in BACKUP_FILES:
            result = _upload_file(service, path, folder_id)
            if result:
                results.append({"file": path.name, "status": "ok"})
            else:
                results.append({"file": path.name, "status": "skipped"})

        _write_backup_manifest(service, folder_id, results)

        ok = len([r for r in results if r["status"] == "ok"])
        print(f"[GDrive] Backup complete: {ok}/{len(BACKUP_FILES)} files uploaded")
        return True

    except Exception as e:
        print(f"[GDrive] Backup failed: {e}")
        return False


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Octodamus Google Drive Memory Backup")
    parser.add_argument("--mode", choices=["backup", "restore", "auth", "status"],
                        default="backup")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.mode == "auth":
        print("[GDrive] Starting OAuth flow...")
        _get_service()
        print("[GDrive] Auth complete. Token saved.")

    elif args.mode == "backup":
        backup()

    elif args.mode == "restore":
        restore_latest(dry_run=args.dry_run)

    elif args.mode == "status":
        try:
            service = _get_service()
            folder_id = _get_or_create_folder(service, FOLDER_NAME)
            results = service.files().list(
                q=f"'{folder_id}' in parents and trashed=false",
                fields="files(name, modifiedTime, size)"
            ).execute()
            files = results.get("files", [])
            print(f"\nOctodamus-Memory on Google Drive ({len(files)} files):")
            for f in sorted(files, key=lambda x: x.get("modifiedTime", ""), reverse=True):
                size = f.get("size", "—")
                print(f"  {f['name']:<35} {f['modifiedTime'][:16]}  {size} bytes")
        except Exception as e:
            print(f"[GDrive] Status failed: {e}")
