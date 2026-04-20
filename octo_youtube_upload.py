"""
octo_youtube_upload.py
YouTube Data API v3 uploader for Octodamus HyperFrames videos.
Reuses gdrive_credentials.json (same Google account, same OAuth flow).

Usage:
    python octo_youtube_upload.py auth                          # first-time OAuth
    python octo_youtube_upload.py upload <mp4> --title "..."   # upload as private
    python octo_youtube_upload.py upload <mp4> --title "..." --public
    python octo_youtube_upload.py quota                         # quota info

Requirements:
    pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib
"""

import sys
from pathlib import Path

ROOT             = Path(__file__).parent
CREDENTIALS_FILE = ROOT / "gdrive_credentials.json"
TOKEN_FILE       = ROOT / "youtube_token.json"

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]

DEFAULT_TAGS = ["octodamus", "crypto", "bitcoin", "market signals", "oracle", "trading"]


def _get_credentials():
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request

    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
        print(f"Token saved: {TOKEN_FILE}")

    return creds


def _build_service():
    from googleapiclient.discovery import build
    return build("youtube", "v3", credentials=_get_credentials())


def upload(mp4_path: str, title: str, description: str = "", tags: list = None, privacy: str = "private") -> str:
    from googleapiclient.http import MediaFileUpload

    path = Path(mp4_path)
    if not path.exists():
        print(f"File not found: {path}")
        sys.exit(1)

    service = _build_service()

    body = {
        "snippet": {
            "title":       title[:100],
            "description": description[:5000],
            "tags":        tags or DEFAULT_TAGS,
            "categoryId":  "28",   # Science & Technology
        },
        "status": {
            "privacyStatus":           privacy,
            "selfDeclaredMadeForKids": False,
        },
    }

    print(f"Uploading: {path.name} ({path.stat().st_size / 1_000_000:.1f} MB)")
    print(f"Title:   {title}")
    print(f"Privacy: {privacy}")

    media = MediaFileUpload(str(path), mimetype="video/mp4", resumable=True, chunksize=5 * 1024 * 1024)
    req = service.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status, response = req.next_chunk()
        if status:
            print(f"  {int(status.progress() * 100)}%", end="\r")

    video_id = response["id"]
    print(f"\nDone: https://www.youtube.com/watch?v={video_id}")
    print(f"Go to YouTube Studio to add thumbnail and publish when ready.")
    return video_id


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)

    cmd = args[0]

    if cmd == "auth":
        print("OAuth flow starting — browser will open...")
        _get_credentials()
        print("Auth complete.")

    elif cmd == "upload":
        if len(args) < 2:
            print("Usage: python octo_youtube_upload.py upload <mp4> --title 'Title' [--public]")
            sys.exit(1)
        mp4    = args[1]
        title  = ""
        desc   = ""
        public = "--public" in args
        i = 2
        while i < len(args):
            if args[i] == "--title" and i + 1 < len(args):
                title = args[i + 1]; i += 2
            elif args[i] == "--description" and i + 1 < len(args):
                desc = args[i + 1]; i += 2
            else:
                i += 1
        if not title:
            title = Path(mp4).stem.replace("-", " ").replace("_", " ").title()
        upload(mp4, title=title, description=desc, privacy="public" if public else "private")

    elif cmd == "quota":
        print("YouTube API: 10,000 units/day free tier")
        print("Cost per upload: ~1,600 units (~6 uploads/day max)")
        print("Check: https://console.cloud.google.com/apis/api/youtube.googleapis.com/quotas")

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
