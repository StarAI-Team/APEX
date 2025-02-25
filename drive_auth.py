from google.oauth2 import service_account
from googleapiclient.discovery import build

# def authenticate_drive():
#     """Authenticate and return the Google Drive service."""
#     SCOPES = ["https://www.googleapis.com/auth/drive.file"]
#     SERVICE_ACCOUNT_FILE = "credentials.json"  

#     credentials = service_account.Credentials.from_service_account_file(
#         SERVICE_ACCOUNT_FILE, scopes=SCOPES
#     )

#     return build("drive", "v3", credentials=credentials)

from google.oauth2 import service_account
from googleapiclient.discovery import build
import logging

def authenticate_drive():
    """Authenticate and return the Google Drive service."""
    try:
        SCOPES = ["https://www.googleapis.com/auth/drive"]
        SERVICE_ACCOUNT_FILE = "credentials.json"

        credentials = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES
        )

        drive_service = build("drive", "v3", credentials=credentials)

        logging.info("✅ Google Drive Authentication Successful!")
        return drive_service

    except Exception as e:
        logging.error(f"❌ Google Drive Authentication Failed: {e}")
        return None  # Prevent bot from proceeding if authentication fails
