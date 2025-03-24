import requests
import os
import time
from dotenv import load_dotenv
import traceback

# Load environment variables
load_dotenv()
API_KEY = os.getenv("OPENAI_API_KEY")
ASSISTANT_ID = os.getenv("OPENAI_ASSISTANT_ID")  # Replace with your actual Assistant ID

import requests
import logging
import os

WHATSAPP_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN")  # Ensure this is loaded

def upload_file(media_url_or_path):
    """
    Uploads an image to OpenAI, either from a URL or a local file path.
    """
    temp_filename = "./temp_image.jpg"  # Use a temporary file path

    # Check if the input is a file path or a URL
    if os.path.isfile(media_url_or_path):
        # ✅ Handle local file path
        logging.info(f"📥 Attempting to upload image from local file: {media_url_or_path}")
        temp_filename = media_url_or_path  # Use the local file path directly
    else:
        # ✅ Handle URL download
        logging.info(f"📥 Attempting to download image from URL: {media_url_or_path}")

        # ✅ Step 1: Download Image from URL (with retry)
        retries = 3
        for attempt in range(retries):
            try:
                headers = {"Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}"}  # Add auth
                response = requests.get(media_url_or_path, headers=headers, stream=True)

                if response.status_code == 200:
                    with open(temp_filename, "wb") as file:
                        for chunk in response.iter_content(1024):
                            file.write(chunk)
                    logging.info(f"✅ Image successfully downloaded: {temp_filename}")
                    break  # Exit loop if successful
                elif response.status_code == 401:
                    logging.error("❌ Unauthorized: Invalid or expired access token.")
                    return None
                elif response.status_code == 403:
                    logging.error("❌ Forbidden: WhatsApp API may be rejecting the request.")
                    return None
                elif response.status_code == 404:
                    logging.error("❌ Media URL not found. It may have expired.")
                    return None
                elif response.status_code == 500:
                    logging.warning(f"⚠️ WhatsApp API Internal Error (500). Retry {attempt+1}/{retries}{response}")
                    time.sleep(2)  # Wait before retrying
                else:
                    logging.error(f"❌ Failed to download image. Status: {response.status_code}")
                    return None
            except Exception as e:
                traceback.print_exc()
                logging.error(f"❌ Error downloading image: {e}")
                return None
        else:
            logging.error("❌ Exhausted retries. Image download failed.")
            return None

    # ✅ Step 2: Upload Image to OpenAI
    try:
        logging.info("🚀 Uploading image to OpenAI API...")

        url = "https://api.openai.com/v1/files"
        headers = {"Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}"}
        response_data = None
        with open(temp_filename, "rb") as file:
            files = {"file": file}
            data = {"purpose": "assistants"}
            response = requests.post(url, headers=headers, files=files, data=data)
            response_data = response.json()

        

        file_id = response_data.get("id")
        if file_id:
            logging.info(f"✅ File uploaded successfully! File ID: {file_id}")
        else:
            logging.error(f"❌ File upload failed: {response_data}")
            return None
    except Exception as e:
        traceback.print_exc()
        logging.error(f"❌ Error uploading file to OpenAI: {e}")
        return None
    finally:
        # ✅ Clean up temporary file if downloaded from URL
        if os.path.isfile(temp_filename) and temp_filename != media_url_or_path:
            os.remove(temp_filename)
            logging.info(f"🗑️ Temporary file {temp_filename} deleted after upload.")

    return file_id


def create_thread():
    """Creates a new thread for communication with the assistant."""
    url = "https://api.openai.com/v1/threads"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "OpenAI-Beta": "assistants=v2"
    }

    response = requests.post(url, headers=headers, json={})
    response_data = response.json()

    if "error" in response_data:
        print(f"❌ Error creating thread: {response_data['error']['message']}")
        return None

    return response_data.get("id")

def run_assistant(thread_id):
    """Starts the assistant to process the uploaded image."""
    url = f"https://api.openai.com/v1/threads/{thread_id}/runs"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "OpenAI-Beta": "assistants=v2"
    }
    data = {"assistant_id": ASSISTANT_ID}

    response = requests.post(url, headers=headers, json=data)
    response_data = response.json()

    if "error" in response_data:
        print(f"❌ Error running assistant: {response_data['error']['message']}")
        return None

    run_id = response_data.get("id")
    
    print(f"🟢 Assistant started processing. Run ID: {run_id}")
    time.sleep(5)  # Allow time for processing before fetching response

    return run_id

def send_file_to_thread(thread_id, file_id):
    """Sends an image file to OpenAI with a universal validation prompt."""
    url = f"https://api.openai.com/v1/threads/{thread_id}/messages"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "OpenAI-Beta": "assistants=v2"
    }

    # **📝 Universal Prompt for OpenAI**
    prompt_text = (
    "A user has uploaded an image. Assess its relevance based on the context of our ongoing conversation and respond accordingly keep it short and consice:\n\n"
    "- **For Vehicle Images (Towing or Assistance Requests):** Acknowledge receipt, confirm visibility,.\n"
    "- **For Identification Documents (National ID, Passport, or Driver’s License):** Ensure clarity and extract the following details concisely:\n"
    "  - *Name on the Document*\n"
    "  - *Document Number*\n"
    "  - Confirm document is valid and successfully uploaded.\n"
    "- **For Proof of Payment Documents: *acknowledge receipt of POP and that It is being validated, admin will get back to them shortly, Extract\n"
    "  - *Name on the POP*\n"
    "  - *Transaction or Reference Number on the POP*\n"
    "- **If the image is unclear**, kindly request the user to upload a clearer version.\n"
    "- **If the image is unrelated**, politely inform the user and provide guidance on the required document.\n\n"
    "DO not put any trigger words or try to send payment details"
    "**Response Format:**\n"
    "✅ Thank you, {user_name}! Your {document_type} has been uploaded successfully.\n\n"
    "📌 *Captured Details:*\n"
    "*Name:* {extracted_name}\n"
    "*Document No.:* {extracted_number}\n\n"
)


    data = {
        "role": "user",
        "content": [
            {"type": "text", "text": prompt_text},
            {"type": "image_file", "image_file": {"file_id": file_id}}
        ]
    }

    response = requests.post(url, headers=headers, json=data)
    response_data = response.json()

    if "error" in response_data:
        print(f"❌ Error sending file: {response_data['error']['message']}")
        return None

    return response_data.get("id")





def get_response(thread_id):
    """Fetches and extracts the assistant's response for the processed file."""
    url = f"https://api.openai.com/v1/threads/{thread_id}/messages"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "OpenAI-Beta": "assistants=v2"
    }

    print("⏳ Fetching assistant response...")

    response = requests.get(url, headers=headers)
    response_data = response.json()

    #print("\n🔍 Full Debug Response:")
    #print(response_data)  # Print full response for debugging

    messages = response_data.get("data", [])

    if not messages:
        #print("\n❌ No messages found in assistant's response.")
        return "⚠️ No response received from the assistant."

    for message in messages:
        if message.get("role") == "assistant":  # Ensure it's an assistant message
            content_list = message.get("content", [])

            for content in content_list:
                if content.get("type") == "text":
                    extracted_text = content["text"]["value"]
                    print(extracted_text)
                    return extracted_text  # Immediately return the first valid response

    return "⚠️ No response received from the assistant."



if __name__ == "__main__":
    file_path = r"C:\Users\user\Pictures\APEX LOGO.png"  # Ensure file path is correct
    file_id = upload_file(file_path)

    if file_id:
        #print(f"✅ File uploaded successfully! File ID: {file_id}")

        # Step 1: Create a thread
        thread_id = create_thread()
        if not thread_id:
            #print("❌ Failed to create thread.")
            exit()

        # Step 2: Send file to the thread
        message_id = send_file_to_thread(thread_id, file_id)
        if not message_id:
            #print("❌ Failed to send file to assistant.")
            exit()

        # Step 3: Run assistant to process the file
        run_id = run_assistant(thread_id)
        if not run_id:
            #print("❌ Failed to run assistant.")
            exit()

        # Step 4: Get the extracted text response
        extracted_text = get_response(thread_id)
        #print("\n📜 Extracted Text:\n", extracted_text)

    else:
        print("❌ File upload failed.")
