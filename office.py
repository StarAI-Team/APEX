import requests
import os
import time
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
API_KEY = os.getenv("OPENAI_API_KEY")
ASSISTANT_ID = os.getenv("OPENAI_ASSISTANT_ID")  # Replace with your actual Assistant ID

import requests
import logging
import os

WHATSAPP_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN")  # Ensure this is loaded

def upload_file(media_url):
    """
    Downloads the image from a URL, saves it locally, and uploads it to OpenAI.
    """
    logging.info(f"📥 Attempting to download image from URL: {media_url}")

    temp_filename = "/tmp/temp_image.jpg"

    # ✅ Step 1: Download Image from WhatsApp (with retry)
    retries = 3
    for attempt in range(retries):
        try:
            headers = {"Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}"}  # Add auth
            response = requests.get(media_url, headers=headers, stream=True)

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
                logging.warning(f"⚠️ WhatsApp API Internal Error (500). Retry {attempt+1}/{retries}")
                time.sleep(2)  # Wait before retrying
            else:
                logging.error(f"❌ Failed to download image. Status: {response.status_code}")
                return None
        except Exception as e:
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
        files = {"file": open(temp_filename, "rb")}
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
        logging.error(f"❌ Error uploading file to OpenAI: {e}")
        return None
    finally:
        os.remove(temp_filename)  # ✅ Clean up temporary file

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
        "A user has uploaded an image. Determine its relevance based on the context of our ongoing conversation.\n\n"
        "- If the image shows a vehicle, confirm visibility and acknowledge it for towing assistance.\n"
        "- If the image is an identification document (National ID, Passport, or Driver’s License), validate its clarity "
        "and extract the following details:\n"
        "  - Document Number\n"
        "  - Name on the Document\n"
        "- If the image is unclear for any case, ask the user to upload a clearer version.\n"
        "- If the image is unrelated to our conversation, politely inform the user."
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

    print("\n🔍 Full Debug Response:")
    print(response_data)  # Print full response for debugging

    messages = response_data.get("data", [])

    if not messages:
        print("\n❌ No messages found in assistant's response.")
        return "⚠️ No response received from the assistant."

    for message in messages:
        if message.get("role") == "assistant":  # Ensure it's an assistant message
            content_list = message.get("content", [])

            for content in content_list:
                if content.get("type") == "text":
                    extracted_text = content["text"]["value"]
                    return extracted_text  # Immediately return the first valid response

    return "⚠️ No response received from the assistant."



if __name__ == "__main__":
    file_path = r"C:\Users\user\Pictures\APEX LOGO.png"  # Ensure file path is correct
    file_id = upload_file(file_path)

    if file_id:
        print(f"✅ File uploaded successfully! File ID: {file_id}")

        # Step 1: Create a thread
        thread_id = create_thread()
        if not thread_id:
            print("❌ Failed to create thread.")
            exit()

        # Step 2: Send file to the thread
        message_id = send_file_to_thread(thread_id, file_id)
        if not message_id:
            print("❌ Failed to send file to assistant.")
            exit()

        # Step 3: Run assistant to process the file
        run_id = run_assistant(thread_id)
        if not run_id:
            print("❌ Failed to run assistant.")
            exit()

        # Step 4: Get the extracted text response
        extracted_text = get_response(thread_id)
        print("\n📜 Extracted Text:\n", extracted_text)

    else:
        print("❌ File upload failed.")
