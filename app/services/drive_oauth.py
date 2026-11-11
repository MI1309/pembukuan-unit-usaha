from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from app.config import config

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]


def _credential_paths() -> tuple[Path, Path]:
    return Path(config.DRIVE_OAUTH_CLIENT_FILE), Path(config.DRIVE_OAUTH_TOKEN_FILE)


def get_drive_credentials() -> Credentials:
    client_file, token_file = _credential_paths()
    if not token_file.is_file():
        raise RuntimeError(
            f"Google Drive OAuth token is missing ({token_file}). "
            "Run `python -m app.auth_drive` once and sign in to your personal Google account."
        )

    credentials = Credentials.from_authorized_user_file(str(token_file), DRIVE_SCOPES)
    if credentials.valid:
        return credentials
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        token_file.write_text(credentials.to_json())
        token_file.chmod(0o600)
        return credentials
    raise RuntimeError(
        "Google Drive OAuth token is invalid or expired. Run `python -m app.auth_drive` again."
    )


def authorize_drive() -> None:
    client_file, token_file = _credential_paths()
    if not client_file.is_file():
        raise RuntimeError(
            f"OAuth Desktop client JSON not found ({client_file}). "
            "Create an OAuth Client ID of type Desktop app in Google Cloud Console, "
            "download its JSON, and save it at this path."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(client_file), DRIVE_SCOPES)
    credentials = flow.run_local_server(
        host="localhost",
        port=0,
        prompt="consent",
        access_type="offline",
        authorization_prompt_message="Open this URL to authorize Google Drive: {url}",
        success_message="Google Drive authorization succeeded. You can close this browser tab.",
    )
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(credentials.to_json())
    token_file.chmod(0o600)
    print(f"Google Drive OAuth token saved to {token_file}. Keep this file private.")
