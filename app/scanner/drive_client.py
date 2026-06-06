"""
Google Drive client for discovering and downloading bank statement PDFs.

Handles OAuth (read-only Drive scope), listing PDFs (whole-drive or within a
folder tree), and downloading selected files to local disk so they can be fed
through the same extraction pipeline as locally-scanned statements.

Requires GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in the environment (from a
Google Cloud OAuth 2.0 "Web application" client).
"""

import io
import json
import os
from pathlib import Path
from typing import Callable, Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from app.scanner.scanner_config import MIN_PDF_SIZE, MAX_PDF_SIZE

# Read-only access so Jar can scan the user's whole Drive or a chosen folder.
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TOKEN_PATH = PROJECT_ROOT / "data" / "google_token.json"

FOLDER_MIME = "application/vnd.google-apps.folder"
PDF_MIME = "application/pdf"


class DriveConfigError(RuntimeError):
    """Raised when Google OAuth client credentials are not configured."""


def _client_config(redirect_uri: str) -> dict:
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise DriveConfigError(
            "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are not set in the environment."
        )
    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }


def build_flow(redirect_uri: str, state: Optional[str] = None) -> Flow:
    """Build an OAuth flow for the authorization-code exchange."""
    return Flow.from_client_config(
        _client_config(redirect_uri),
        scopes=SCOPES,
        redirect_uri=redirect_uri,
        state=state,
    )


def save_credentials(creds: Credentials) -> None:
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(creds.to_json())


def load_credentials() -> Optional[Credentials]:
    """Load stored credentials, refreshing the access token if needed."""
    if not TOKEN_PATH.exists():
        return None
    try:
        data = json.loads(TOKEN_PATH.read_text())
        creds = Credentials.from_authorized_user_info(data, SCOPES)
    except (ValueError, json.JSONDecodeError):
        return None

    if creds.valid:
        return creds
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            save_credentials(creds)
            return creds
        except Exception:
            return None
    return None


def is_connected() -> bool:
    return load_credentials() is not None


def disconnect() -> None:
    if TOKEN_PATH.exists():
        TOKEN_PATH.unlink()


def build_service(creds: Credentials):
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def get_account_email(creds: Credentials) -> Optional[str]:
    try:
        service = build_service(creds)
        about = service.about().get(fields="user(emailAddress)").execute()
        return about.get("user", {}).get("emailAddress")
    except Exception:
        return None


def list_folders(creds: Credentials) -> list[dict]:
    """List the user's Drive folders for the folder picker."""
    service = build_service(creds)
    folders: list[dict] = []
    page_token = None
    while True:
        resp = service.files().list(
            q=f"mimeType='{FOLDER_MIME}' and trashed=false",
            spaces="drive",
            fields="nextPageToken, files(id, name)",
            pageSize=1000,
            pageToken=page_token,
        ).execute()
        folders.extend(
            {"id": f["id"], "name": f["name"]} for f in resp.get("files", [])
        )
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    folders.sort(key=lambda f: f["name"].lower())
    return folders


def _collect_subfolder_ids(service, root_id: str) -> set[str]:
    """Return root_id plus all descendant folder ids (recursive)."""
    ids = {root_id}
    frontier = [root_id]
    while frontier:
        parent = frontier.pop()
        page_token = None
        while True:
            resp = service.files().list(
                q=f"'{parent}' in parents and mimeType='{FOLDER_MIME}' and trashed=false",
                spaces="drive",
                fields="nextPageToken, files(id)",
                pageSize=1000,
                pageToken=page_token,
            ).execute()
            for f in resp.get("files", []):
                if f["id"] not in ids:
                    ids.add(f["id"])
                    frontier.append(f["id"])
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
    return ids


def list_pdf_files(
    creds: Credentials,
    folder_id: Optional[str] = None,
    progress_cb: Optional[Callable[[int], None]] = None,
) -> list[dict]:
    """List PDF files in the user's Drive.

    Args:
        creds: Authorized credentials.
        folder_id: If given, restrict to this folder and its subfolders.
        progress_cb: Optional callback invoked with the running PDF count.

    Returns:
        List of {file_id, name, size} dicts passing the size filters.
    """
    service = build_service(creds)

    parent_ids: Optional[set[str]] = None
    if folder_id:
        parent_ids = _collect_subfolder_ids(service, folder_id)

    files: list[dict] = []
    page_token = None
    while True:
        resp = service.files().list(
            q=f"mimeType='{PDF_MIME}' and trashed=false",
            spaces="drive",
            fields="nextPageToken, files(id, name, size, parents)",
            pageSize=1000,
            pageToken=page_token,
        ).execute()

        for f in resp.get("files", []):
            if parent_ids is not None:
                parents = set(f.get("parents", []))
                if parents.isdisjoint(parent_ids):
                    continue
            try:
                size = int(f.get("size", 0))
            except (TypeError, ValueError):
                size = 0
            if size and (size < MIN_PDF_SIZE or size > MAX_PDF_SIZE):
                continue
            files.append({"file_id": f["id"], "name": f["name"], "size": size})
            if progress_cb:
                progress_cb(len(files))

        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    return files


def download_file(creds: Credentials, file_id: str, dest_path: Path) -> Path:
    """Download a Drive file to dest_path."""
    service = build_service(creds)
    request = service.files().get_media(fileId=file_id)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with io.FileIO(str(dest_path), "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    return dest_path
