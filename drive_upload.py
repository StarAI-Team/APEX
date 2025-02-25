import requests
import io
from googleapiclient.http import MediaIoBaseUpload
from drive_auth import authenticate_drive
import logging
import os

# def upload_to_google_drive(file_url, file_name):
#     """Download an image from a URL, upload it to Google Drive, and make it public."""
#     drive_service = authenticate_drive()

#     # Download the image from the given URL
#     response = requests.get(file_url)
#     if response.status_code != 200:
#         return None

#     file_content = io.BytesIO(response.content)  # Convert to binary file

#     file_metadata = {
#         "name": file_name  # File will be stored in My Drive
#     }

#     media = MediaIoBaseUpload(file_content, mimetype="image/jpeg")

#     # Upload the file
#     file = drive_service.files().create(
#         body=file_metadata, media_body=media, fields="id, webViewLink"
#     ).execute()

#     file_id = file.get("id")
#     file_link = file.get("webViewLink")

#     # 🔹 Change File Permissions to Public
#     public_permission = {
#         "type": "anyone",
#         "role": "reader"
#     }
#     drive_service.permissions().create(fileId=file_id, body=public_permission).execute()

#     return file_link  # Return the Google Drive shareable link
def upload_to_google_drive(file_url, file_name):
    """Downloads an image from a WhatsApp URL and uploads it to Google Drive."""
    drive_service = authenticate_drive()

    if not drive_service:
        logging.error("❌ Google Drive authentication failed. Cannot upload.")
        return None

    # ✅ Pass WhatsApp API Token in Headers when downloading
    headers = {
        "Authorization": f"Bearer {os.getenv('META_ACCESS_TOKEN')}"  # Use the correct WhatsApp API Token
    }

    response = requests.get(file_url, headers=headers, stream=True)
    if response.status_code != 200:
        logging.error(f"❌ Failed to download file: {file_url} - Status Code: {response.status_code}")
        return None

    file_content = io.BytesIO(response.content)
    logging.info(f"✅ File downloaded successfully: {file_name}")

    # ✅ Upload to Google Drive
    file_metadata = {
        "name": file_name
    }

    media = MediaIoBaseUpload(file_content, mimetype="image/jpeg")

    try:
        file = drive_service.files().create(
            body=file_metadata, media_body=media, fields="id, webViewLink"
        ).execute()

        file_link = file.get("webViewLink")

        # ✅ Make file public
        public_permission = {"type": "anyone", "role": "reader"}
        drive_service.permissions().create(fileId=file.get("id"), body=public_permission).execute()

        logging.info(f"✅ Uploaded to Google Drive: {file_link}")
        return file_link

    except Exception as e:
        logging.error(f"❌ Google Drive Upload Failed: {e}")
        return None
