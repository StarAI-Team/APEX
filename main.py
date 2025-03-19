import os
import re
import schedule
import time
import threading
import logging
from flask import Flask, request, session, jsonify, json, make_response
from openai import OpenAI
import requests
from flask_session import Session
from datetime import datetime, timedelta
import psycopg2
from dotenv import load_dotenv
import traceback
from office import upload_file, create_thread, send_file_to_thread, run_assistant, get_response  # Import office.py functions
from flask import Flask, request, jsonify, Response
from drive_upload import upload_to_google_drive
import uuid
import time  # For timestamps
from logging.handlers import RotatingFileHandler



# Load environment variables from .env file
load_dotenv()


rental_sessions = {}
towing_sessions = {}
travel_sessions = {}

processed_messages = set()  # ✅ Stores processed message IDs to prevent duplicates



# Set up environment variables for Meta API
os.environ['META_PHONE_NUMBER_ID'] 
os.environ['META_ACCESS_TOKEN']
os.environ['OPENAI_API_KEY'] 



 #Initialize OpenAI client
client = OpenAI(
    api_key=os.environ.get("OPENAI_API_KEY")
)

OPENAI_ASSISTANT_ID = os.getenv("OPENAI_ASSISTANT_ID")

# ✅ Configure log rotation to prevent log file from growing indefinitely
log_handler = RotatingFileHandler(
    "agent.log", maxBytes=5*1024*1024, backupCount=2  # 5MB log file, keeps 2 backups
)

log_handler.setLevel(logging.INFO)  # ✅ Ignores DEBUG logs

logging.basicConfig(
    handlers=[log_handler],
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO  # ✅ Only log INFO, WARNING, ERROR, CRITICAL
)

# ✅ Suppress Debug Logging from External Libraries (e.g., OpenAI, Flask, HTTP Requests)
logging.getLogger('werkzeug').setLevel(logging.WARNING)  # Flask's default server logs
logging.getLogger('inotify.adapters').setLevel(logging.WARNING)  # Inotify logging spam
logging.getLogger('inotify').setLevel(logging.WARNING)
logging.getLogger('httpx').setLevel(logging.WARNING)  # Suppresses OpenAI API spam
logging.getLogger('openai').setLevel(logging.WARNING)  # Suppresses OpenAI debug logs
logging.getLogger('urllib3').setLevel(logging.WARNING)  # Suppresses API request logs

# Flask application
app = Flask(__name__)

# Configure Flask-Session
app.config['SESSION_TYPE'] = 'filesystem'  # Use filesystem for development
app.config['SESSION_PERMANENT'] = False  # Sessions will not expire unless explicitly cleared 
app.config['SESSION_USE_SIGNER'] = True
app.secret_key = 'ghnvdre5h4562' 
Session(app)



# Admin number to send periodic updates
ADMIN_NUMBER = os.getenv('ADMIN_NUMBER')



def get_db_connection():
    """Creates and returns a PostgreSQL database connection."""
    try:
        conn = psycopg2.connect(
            dbname=os.getenv('DB_NAME'),
            user=os.getenv('DB_USER'),
            password=os.getenv('DB_PASSWORD'),
            host=os.getenv('DB_HOST'),
            port=os.getenv('DB_PORT')
            )
        return conn
    except psycopg2.Error as e:
        logging.error(f"Database connection error: {e}")
        return None

# Log new conversation or updates in the database
def log_conversation(from_number, message, bot_reply, status):
    """
    Log customer conversations in the database.
    """
    conn = get_db_connection()  # Use PostgreSQL connection
    if not conn:
        logging.info("❌ Database connection failed. Cannot log conversation.")
        return

    try:
        cursor = conn.cursor()
        logging.info(f"📝 Logging conversation: {from_number} - {message} - {bot_reply} - {status}")
        cursor.execute('''
            INSERT INTO conversations (from_number, message, bot_reply, status, reported)
            VALUES (%s, %s, %s, %s, 0)
        ''', (from_number, message, bot_reply, status))
        conn.commit()
        logging.info("✅ Conversation logged successfully.")
    
    except psycopg2.Error as e:
        logging.warning(f"Database error while logging conversation: {e}")
    
    finally:
        cursor.close()
        conn.close()  # Ensure connection is closed

def get_customer_name(contact_number):
    """
    Retrieve the customer's name from the database using their WhatsApp number.
    """
    try:
        # Ensure we're querying with the correct phone number
        if contact_number.startswith('whatsapp:'):
            contact_number = contact_number.replace('whatsapp:', '') 

        # Normalize contact number (Ensure +.. format)
        if not contact_number.startswith('+'):
            normalized_contact = f"+{contact_number}"
        else:
            normalized_contact = contact_number

        conn = get_db_connection()  # Use PostgreSQL connection
        if not conn:
            return "Guest"  # Return a default name if connection fails

        cursor = conn.cursor()
        logging.debug(f"Debug: Querying database with contact_number: {normalized_contact} or {contact_number}")
        
        # Query for both formats
        cursor.execute(
            'SELECT name FROM apex_customers WHERE contact_number IN (%s, %s)',
            (normalized_contact, contact_number)
        )
        row = cursor.fetchone()

        if row and row[0]:
            return row[0]
        else:
            logging.debug(f"Debug: No matching record found for {contact_number}")
            return "Guest"  # Return a default name instead of None

    except psycopg2.Error as e:
        logging.error(f"Database error while fetching name: {e}")
        return "Guest"

    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        return "Guest"

    finally:
        cursor.close()
        conn.close()  # Ensure connection is closed


def is_valid_phone_number(phone_number): 
    """ Validate international phone numbers (E.164 format). """
    pattern = re.compile(r"^\+[1-9]\d{6,14}$")  # Correct E.164 format
    return bool(pattern.match(phone_number))



def get_user_thread(from_number):
    """Retrieves the user's existing thread ID from the database."""
    conn = get_db_connection()
    if not conn:
        return None

    try:
        cursor = conn.cursor()
        cursor.execute("SELECT thread_id FROM threads WHERE from_number = %s", (from_number,))
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        return result[0] if result else None
    except psycopg2.Error as e:
        logging.error(f"❌ Database error retrieving thread for {from_number}: {e}")
        return None

def save_user_thread(from_number, thread_id):
    """Saves a new thread ID for the user in the database."""
    conn = get_db_connection()
    if not conn:
        return

    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO threads (from_number, thread_id)
            VALUES (%s, %s)
            ON CONFLICT (from_number) DO UPDATE SET thread_id = EXCLUDED.thread_id;
        """, (from_number, thread_id))
        conn.commit()
        cursor.close()
        conn.close()
        logging.info(f"✅ Thread saved for {from_number}: {thread_id}")
    except psycopg2.Error as e:
        logging.error(f"❌ Database error saving thread for {from_number}: {e}")

    
def fetch_conversation_log(from_number, limit=5):
    """
    Fetches the last 'limit' messages from the conversation history for context.
    """
    conn = get_db_connection()
    if not conn:
        return ""

    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT message, bot_reply 
            FROM conversations 
            WHERE from_number = %s 
            ORDER BY timestamp DESC 
            LIMIT %s
        """, (from_number, limit))

        conversation_history = cursor.fetchall()

        # Format conversation log
        log_text = "\n".join([f"User: {msg} \nBot: {reply}" for msg, reply in conversation_history])
        return log_text if log_text else "No previous conversation available."

    except psycopg2.Error as e:
        logging.error(f"❌ Database error while fetching conversation log: {e}")
        return "No conversation history available."
    
    finally:
        cursor.close()
        conn.close()

def create_new_thread(from_number, last_message):
    """
    Creates a new OpenAI thread while ensuring continuity by including past conversation logs.
    Adds a pre-instruction to OpenAI to ensure responses are short and concise.
    """
    conversation_log = fetch_conversation_log(from_number)  # Get the last few messages
    
    session_summary = (
        f"🔄 *Session Summary for {from_number}:* \n"
        f"Previous conversation log:\n{conversation_log}\n\n"
        f"Last user message: {last_message}"
    )

    url = "https://api.openai.com/v1/threads"
    headers = {
        "Authorization": f"Bearer {os.environ.get('OPENAI_API_KEY')}",
        "OpenAI-Beta": "assistants=v2",  # ✅ ADD REQUIRED HEADER
        "Content-Type": "application/json"
    }
    
    payload = {
        "messages": [
            {
                "content": f"This is a new user whose contact number is {from_number}. ASK FOR THE USER NAME FIRST. Respond with 12 words or less and to the point"
                            "(Once name is captured, don't ask for it again when getting service requirements) add emojis but sparingly not all messages. Use emojis here and there"
                            "For rental ALWAYS ask for rent out date and return date, for towing ask for pickup and destination location to give user an estimate towing fee"
                            "For towing ALWAYS send the estimate fee right after getting pickup and destination address and confirm if they are happy with the fee before asking for car image"
                            "After gathering alL details for towing, send the trigger_payment_button fucntion"
                            "If the upload is appropriate to the conversation flow then you can send the payment trigger. e.g. if user sent ID when renting a car then you can then send the payment trigger if not ask them to send the appropriate image first"
                            "Avoid unnecessary details. NB If a car is mentioned whether from user or from you at any point, ALWAYS SEND THE TRIGGER FOR THE CAR MODEL OR CAR CLASS TO SHOW IMAGES",
                "role": "assistant"
            },
            {"content": "All responses must be very short. concise but friendly", "role": "assistant"},  # ✅ Provide context
            {"content": "Hi", "role": "user"}  # ✅ Include last user message
        ]
    }

    response = requests.post(url, headers=headers, json=payload)
    
    if response.status_code == 200:
        thread_id = response.json().get("id")
        save_user_thread(from_number, thread_id)
        logging.info(f"✅ New thread created: {thread_id} for {from_number}")
        return thread_id
    else:
        logging.error(f"❌ Failed to create a new thread: {response.text}")
        return None

def trigger_payment_button(from_number):
    """
    Sends a WhatsApp interactive button message asking the user if they want to pay online or onsite.
    """

    payment_message = "*How would you like to pay?*"

    buttons = [
        {"type": "reply", "reply": {"id": "pay_online", "title": "💳 Pay Online"}},
        {"type": "reply", "reply": {"id": "pay_onsite", "title": "💵 Pay Onsite"}}
    ]

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": from_number,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": payment_message[:1024]},  # Ensure within WhatsApp limits
            "action": {"buttons": buttons}
        }
    }

    logging.info(f"📨 Preparing to send WhatsApp Payment Button to {from_number}")

    # ✅ Ensure payload is properly formatted
    if not payload["interactive"]["body"]["text"].strip():
        logging.warning("⚠️ Payment button message is empty. Skipping WhatsApp request.")
        return False

    # ✅ Send Payment Button
    success = send_whatsapp_interactive_message(from_number, payload)

    if success:
        logging.info(f"✅ Successfully sent payment button to {from_number}")
    else:
        logging.error(f"❌ Failed to send payment button to {from_number}")

    return success



def send_whatsapp_interactive_message(to, payload, max_retries=3):
    """Sends an interactive WhatsApp message, such as payment buttons."""
    start_time = time.time()
    PHONE_NUMBER_ID = os.getenv("META_PHONE_NUMBER_ID")
    ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN")
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"

    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }

    logging.info(f"📨 Sending WhatsApp Interactive Message: {payload}")

    # ✅ Attempt to Send WhatsApp Message with Retries
    for attempt in range(max_retries):
        try:
            response = requests.post(url, headers=headers, json=payload)
            response.raise_for_status()

            execution_time = time.time() - start_time
            logging.info(f"✅ WhatsApp interactive message sent to {to} in {execution_time:.2f} seconds on attempt {attempt + 1}")
            logging.debug(f"📨 WhatsApp API Response: {response.json()}")
            return True

        except requests.RequestException as e:
            logging.warning(f"❌ WhatsApp API Error on attempt {attempt + 1}: {e}")
            time.sleep(2)

    logging.error(f"❌ Failed to send WhatsApp interactive message to {to} after {max_retries} attempts.")
    return False



def handle_payment_selection(from_number, selection_id):
    """
    Handles user payment selection from WhatsApp interactive buttons.
    Queries OpenAI for structured admin summary.
    Extracts relevant details and logs service request in DB.
    """
    ref_number = generate_ref_number()  # ✅ Generate unique ref number

    # ✅ Determine payment method
    if selection_id == "pay_online":
        payment_link = "www.payment-link.com"  # ✅ Replace with actual payment link
        message = (
            f"💳 Follow the link below to complete your payment:\n🔗 {payment_link}\n\n"
            f"Please upload your proof of payment when done.\n"
            f"📋 *Ref Number:* {ref_number}"
        )
        payment_method = "Online Payment"

    elif selection_id == "pay_onsite":
        message = (
            f"✅ Noted! We will be expecting you at our offices.\n"
            f"📋 *Ref Number:* {ref_number}"
        )
        payment_method = "Onsite Payment"

    else:
        message = "❌ Invalid selection. Please try again."
        send_whatsapp_message(from_number, message, is_bot_message=True)
        return False  # ✅ Exit if invalid selection

    # ✅ Send Confirmation Message to User
    send_whatsapp_message(from_number, message, is_bot_message=True)

    # ✅ Generate Admin Summary using OpenAI
    openai_message_admin = (
        f"A user has uploaded an image for processing. These are their details: \n"
        f"📞 Contact: {from_number}\n"
        f"📌 Reference Number: {ref_number}\n"
        f"Payment Method: {payment_method}"
        f"📋 Generate a structured summary including the user's name, Contact, Ref Number,drive link: {drive_links[from_number]} "
        f"Service Type, and any available uploaded document links.\n"
        f"Just keep the summary short e.g\n" 
        """📢 New Car Rental Request!
            👤 Client: Tanaka Mupfurutsa
            📞 Contact: 263784178307
            📸 Image Link: [View Image](https://drive.google.com/file/d/1zIdjXxvh1fKb6AM580uGUmYTKty15N/view?usp=drivesdk)
            📝 Service Type: Toyota Landcruiser 300 Series Rental
            📌 Reference Number: ATT031315
            🔍 Next Steps: User chose to pay online/on-site, kindly check car availability and follow up with client"""
    )
    admin_summary = query_openai_model(openai_message_admin, from_number)

    # ✅ Extract User Name, Service Type, and Drive Link from the Summary
    extracted_name = extract_name(admin_summary)
    extracted_service = extract_service_type(admin_summary)
    drive_link = drive_links[from_number]

    # ✅ Store Service Request in Database
    store_service_request(
        from_number=from_number,
        service_type=extracted_service if extracted_service else "Not Specified",
        ref_number=ref_number,
        user_name=extracted_name if extracted_name else "Unknown",
        drive_link=drive_link if drive_link else "Not Uploaded"
    )

    # ✅ Send Admin Summary
    send_whatsapp_message(ADMIN_NUMBER, admin_summary)

    return True  # ✅ Successfully processed payment selection


def extract_name(summary):
    """Extracts the user's name from the OpenAI-generated admin summary."""
    match = re.search(r"(?i)(?:👤\s*Client:|User\s*Name:|Name:)\s*([\w\s-]+)", summary)
    if match:
        extracted_name = match.group(1).strip()
        logging.info(f"✅ Extracted Name: {extracted_name}")
        return extracted_name
    else:
        logging.warning("⚠️ Failed to extract name from summary.")
        return None


def extract_service_type(summary):
    """Extracts the service type from the OpenAI-generated admin summary."""
    match = re.search(r"(?i)(?:Service Type|🛠):\s*([A-Za-z\s]+)", summary)
    logging.info(f"Extracted service Type: {match}")
    return match.group(1).strip() if match else None

def store_service_request(from_number, service_type, ref_number, user_name, drive_link):
    """
    Stores the service request in the 'services' table.
    """
    try:
        connection = get_db_connection()  # ✅ Establish DB connection
        cursor = connection.cursor()

        sql = """
        INSERT INTO services (wa_id, service, ref_number, name, contact, image_link, details)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """
        cursor.execute(sql, (from_number, service_type, ref_number, user_name, from_number, drive_link, "Pending Review"))
        connection.commit()

        logging.info(f"✅ Service request stored in DB: {ref_number}")

    except Exception as e:
        logging.error(f"❌ Error storing service request: {e}")

    finally:
        cursor.close()
        connection.close()


RUN_TIMEOUT = 15  # Reduce timeout for faster retries
MAX_RETRIES = 3  # Maximum retries before giving up

def query_openai_model(user_message, from_number):
    """
    Queries OpenAI while ensuring thread continuity and switching to a new thread when needed.
    """
    try:
        thread_id = get_user_thread(from_number)

        # ✅ Ensure a valid thread_id is available
        if not thread_id:
            logging.warning(f"⚠️ No thread found for {from_number}. Creating a new one...")
            thread_id = create_new_thread(from_number, user_message)  # Create a new thread immediately
            if not thread_id:
                return "Oops, I think my network glitched. Mind sending that again?"

        # ✅ Check for active runs and resolve them
        new_thread_id = check_and_resolve_active_run(thread_id, from_number, user_message)
        if new_thread_id:
            thread_id = new_thread_id  # Use the newly created thread

        # ✅ Send message to OpenAI
        client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=user_message
        )

        # ✅ Start OpenAI assistant run
        run = client.beta.threads.runs.create(thread_id=thread_id, assistant_id=OPENAI_ASSISTANT_ID)

        start_time = time.time()
        retries = 0

        while run.status not in ["completed", "failed", "canceled"]:
            if time.time() - start_time > RUN_TIMEOUT:
                logging.warning(f"⚠️ OpenAI response took too long ({RUN_TIMEOUT}s). Retrying ({retries + 1}/{MAX_RETRIES})...")

                # ✅ Attempt a retry up to MAX_RETRIES
                if retries < MAX_RETRIES:
                    retries += 1
                    start_time = time.time()  # Reset timeout
                    time.sleep(2)  # Small delay before retrying
                    run = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run.id)
                    continue  # Retry loop

                logging.error(f"❌ OpenAI response timed out after {MAX_RETRIES} retries.")
                return "Oops! Looks like there's a slight delay. Working on it! 🔄"

            time.sleep(1)
            run = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run.id)

        # ✅ Retrieve OpenAI response
        messages = client.beta.threads.messages.list(thread_id=thread_id)

        if not messages.data:
            logging.error("❌ OpenAI did not return a valid response.")
            return "Hmm, I didn’t catch that. Can you say it again?"

        bot_reply = messages.data[0].content[0].text.value if messages.data else "No response available."
        logging.info(f"🤖 Bot Reply: {bot_reply}")

        # ✅ Log the conversation
        log_conversation(from_number, user_message, bot_reply, "processed")

        return bot_reply

    except Exception as e:
        logging.error(f"❌ OpenAI API Error: {str(e)}")
        #send_whatsapp_message(bot_reply)
        return "Hang on..."


def check_and_resolve_active_run(thread_id, from_number, last_message):
    """
    Checks if a thread's OpenAI run is stuck. If so, cancels it and creates a new thread.
    """
    if not thread_id:
        logging.warning(f"⚠️ No valid thread_id for {from_number}. Creating a new one...")
        return create_new_thread(from_number, last_message)

    url = f"https://api.openai.com/v1/threads/{thread_id}/runs"
    headers = {
        "Authorization": f"Bearer {os.environ.get('OPENAI_API_KEY')}",
        "OpenAI-Beta": "assistants=v2"
    }

    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            active_runs = response.json().get("data", [])

            for run in active_runs:
                if run.get("status") == "in_progress":
                    run_id = run.get("id")
                    start_time = time.time()

                    # ✅ Wait up to 120 seconds before canceling
                    while time.time() - start_time < 120:
                        time.sleep(2)
                        latest_run = requests.get(f"{url}/{run_id}", headers=headers).json()
                        
                        if latest_run.get("status") == "completed":
                            return None  # ✅ If it completes, no need to switch threads

                    # ❌ If still running, cancel it
                    logging.warning(f"⚠️ Cancelling stuck OpenAI run: {run_id}")
                    cancel_url = f"https://api.openai.com/v1/threads/{thread_id}/runs/{run_id}/cancel"
                    cancel_response = requests.post(cancel_url, headers=headers)

                    if cancel_response.status_code == 200:
                        logging.info("✅ Successfully canceled the stuck run.")
                    else:
                        logging.error(f"❌ Failed to cancel stuck run: {cancel_response.text}")

                    # 🔄 Create a new thread with context
                    new_thread_id = create_new_thread(from_number, last_message)
                    if new_thread_id:
                        logging.info(f"🔄 New thread created: {new_thread_id}")
                        return new_thread_id  # Return new thread ID

        elif response.status_code == 400:
            logging.warning(f"⚠️ OpenAI API returned 400 - Possible invalid thread ID, creating a new thread for {from_number}...")
            return create_new_thread(from_number, last_message)

        else:
            logging.error(f"❌ OpenAI API Error: {response.status_code} - {response.text}")

    except requests.exceptions.RequestException as e:
        logging.error(f"❌ Failed to check runs due to network error: {e}")
    
    return None  # No need to switch threads if there's no active run

def process_pending_responses():
    """
    Periodically checks if the network is available and retries sending stored messages (both text and file messages).
    """
    pending_responses = get_pending_responses()

    for response in pending_responses:
        from_number, user_message, response_id = response["from_number"], response["message"], response["id"]
        bot_reply = query_openai_model(user_message, from_number)  

        if bot_reply:
            send_whatsapp_message(from_number, bot_reply, is_bot_message=True)
            mark_response_as_sent(response_id)  # ✅ Mark text response as processed

    # ✅ Now also check for pending file messages
    pending_files = get_pending_file_responses()

    for file_response in pending_files:
        message_id, recipient, file_url, file_type, caption, retries = file_response

        logging.info(f"🔄 Retrying file send to {recipient} (Attempt {retries})...")

        success = send_whatsapp_file(recipient, file_url, file_type=file_type, caption=caption)

        if success:
            mark_file_as_sent(message_id)  # ✅ Mark file as successfully sent
            logging.info(f"✅ Successfully resent file to {recipient}. Removed from pending messages.")
        else:
            increment_file_retry_count(message_id)  # ✅ Increase retry count
            logging.warning(f"⚠️ File send to {recipient} failed again. Will retry later.")

schedule.every(5).minutes.do(process_pending_responses)  # ✅ Retry every 5 minutes

def mark_file_as_sent(message_id):
    """
    Marks a stored file message as successfully sent in the database.
    """
    conn = get_db_connection()
    if not conn:
        logging.error(f"❌ Could not connect to database to update file message {message_id}")
        return

    try:
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE pending_responses 
            SET status = 'sent' 
            WHERE id = %s
        """, (message_id,))

        conn.commit()
        logging.info(f"✅ Marked file message {message_id} as sent.")

    except psycopg2.Error as e:
        logging.error(f"❌ Database error updating file message status: {e}")

    finally:
        cursor.close()
        conn.close()


def mark_response_as_sent(response_id):
    """
    Marks a stored message as processed after it has been successfully sent.
    """
    conn = get_db_connection()
    if not conn:
        logging.error(f"❌ Could not connect to database to update response {response_id}")
        return

    try:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE pending_responses SET processed = TRUE WHERE id = %s
        """, (response_id,))
        conn.commit()
        logging.info(f"✅ Marked response {response_id} as processed.")
    except psycopg2.Error as e:
        logging.error(f"❌ Database error updating response status: {e}")
    finally:
        cursor.close()
        conn.close()

def increment_file_retry_count(message_id):
    """
    Increments the retry count for a file message that failed to send.
    If retries exceed a certain limit, mark it as 'failed'.
    """
    conn = get_db_connection()
    if not conn:
        logging.error(f"❌ Could not connect to database to update retry count for {message_id}")
        return

    try:
        cursor = conn.cursor()

        # ✅ Update retry count, but if retries exceed limit (e.g., 5), mark as 'failed'
        cursor.execute("""
            UPDATE pending_responses 
            SET retries = retries + 1, 
                status = CASE 
                            WHEN retries + 1 >= 5 THEN 'failed' 
                            ELSE 'pending' 
                         END
            WHERE id = %s
        """, (message_id,))

        conn.commit()
        logging.info(f"🔄 Increased retry count for file message {message_id}")

    except psycopg2.Error as e:
        logging.error(f"❌ Database error updating retry count: {e}")

    finally:
        cursor.close()
        conn.close()


def get_pending_responses():
    """
    Retrieves all messages that failed to process and need to be sent later.
    """
    conn = get_db_connection()
    if not conn:
        logging.error("❌ Could not connect to database to fetch pending responses.")
        return []

    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, from_number, message FROM pending_responses WHERE processed = FALSE
        """)
        pending_responses = [{"id": row[0], "from_number": row[1], "message": row[2]} for row in cursor.fetchall()]
        return pending_responses
    except psycopg2.Error as e:
        logging.error(f"❌ Database error retrieving pending responses: {e}")
        return []
    finally:
        cursor.close()
        conn.close()

def get_pending_file_responses():
    """
    Fetches all pending file messages that have not exceeded max retries.
    """
    conn = get_db_connection()
    if not conn:
        logging.error("❌ Could not connect to database to fetch pending file responses.")
        return []

    try:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, recipient, file_url, file_type, caption, retries 
            FROM pending_responses 
            WHERE status = 'pending' AND retries < 3
        """)

        pending_files = cursor.fetchall()
        return pending_files

    except psycopg2.Error as e:
        logging.error(f"❌ Database error retrieving pending file messages: {e}")
        return []
    
    finally:
        cursor.close()
        conn.close()



def save_pending_response(recipient, file_url, file_type="image", caption=None, retries=0):
    """
    Saves a failed file message to be retried later.
    """
    conn = get_db_connection()
    if not conn:
        logging.error(f"❌ Could not connect to database to save pending response for {recipient}")
        return   

    try:
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO pending_responses (recipient, file_url, file_type, caption, retries, status)
            VALUES (%s, %s, %s, %s, %s, 'pending')
        """, (recipient, file_url, file_type, caption, retries))

        conn.commit()
        logging.info(f"💾 Saved failed file send for {recipient}, will retry later.")

    except psycopg2.Error as e:
        logging.error(f"❌ Database error saving pending file message: {e}")

    finally:
        cursor.close()
        conn.close()  # ✅ Always close the connection!


# Dictionary to store the last sent message per user
last_sent_messages = {}

processed_triggers = set()  # ✅ Store processed trigger messages to prevent duplication

def send_whatsapp_message(to, message=None, max_retries=3, is_bot_message=False):
    """Sends a WhatsApp message while ensuring triggers are processed only once and avoids repeating recent messages."""
    start_time = time.time()
    PHONE_NUMBER_ID = os.getenv("META_PHONE_NUMBER_ID")
    ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN")
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"

    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }

    if not message:
        logging.warning("⚠️ Attempted to send an empty message. Skipping WhatsApp request.")
        return False

    message_clean = message.strip() if isinstance(message, str) else ""

    logging.info(f"📨 Processing message: '{message_clean}' from {to}")

    # ✅ Check if the message is already in user_message_history (to avoid repeating messages)
    # if to in user_message_history and message_clean in user_message_history[to]:
    #     logging.info(f"🔄 Message '{message_clean}' was recently sent. Sending fallback response instead.")
    #     send_text_message(to, "Hang on...")  # ✅ Send fallback response
    #     return True  # ✅ Stop further processing
    

    # ✅ Store the message in user_message_history
    if to not in user_message_history:
        user_message_history[to] = []
    user_message_history[to].append(message_clean)

    # ✅ Keep only the last 5 messages to prevent infinite loops
    if len(user_message_history[to]) > 5:
        user_message_history[to].pop(0)

    # ✅ Define Trigger Words
    trigger_words = {
        "trigger_r", "trigger_rent_a_car", "trigger_tracking_service", "trigger_travel_booking",
        "trigger_towing_request", "trigger_honda_fit_rs", "trigger_honda_fit_gk3", "trigger_payment_button",
        "trigger_toyota_aqua", "trigger_honda_vezel", "trigger_mazda_cx5", "trigger_x-trail",
        "trigger_trail_blazer", "trigger_toyota_landcruiser_300_series", "trigger_toyota_prado", 
        "trigger_nissan_navara", "trigger_toyota_hilux_d4d", "trigger_ford_ranger", "trigger_honda_fits",
        "trigger_isuzu_x-rider", "trigger_nv350_9_seater", "trigger_nv350_13_seater", "trigger_toyota_fortuner",
        "trigger_1", "trigger_2", "trigger_3", "trigger_4", "trigger_5", "trigger_gd6_hilux",
        "trigger_suv", "trigger_fuel_saver", "trigger_double_cab", "trigger_mid_suv",
        "trigger_mini_buses", "trigger_luxury", "trigger_axio", "trigger_mercedes_benz_e_class",
        "trigger_mercedes_benz_c_class", "trigger_all_car_images", "trigger_toyota_gd6_hilux"
    }

    # ✅ Detect Trigger Word
    detected_trigger = next((trigger for trigger in trigger_words if re.search(rf'\b{trigger}\b', message_clean)), None)

    if detected_trigger:
        logging.info(f"🛑 Detected trigger word '{detected_trigger}' in message.")

        # ✅ If from bot, send trigger directly to webhook (DO NOT request another bot reply)
        if is_bot_message:
            send_to_webhook(to, detected_trigger)
            logging.info(f"✅ Successfully sent trigger word '{detected_trigger}' to webhook.")
            message_without_trigger = message_clean.replace(detected_trigger, "").strip()
            if message_without_trigger:
                logging.info(f"📨 Sending full message to user before trigger: '{message_without_trigger}'")
                send_text_message(to, message_without_trigger)
            return True  # ✅ Exit here so we don't request another bot reply

        # ✅ If from user, process normally (send message & trigger separately)
        else:
            message_without_trigger = message_clean.replace(detected_trigger, "").strip()
            if message_without_trigger:
                logging.info(f"📨 Sending full message to user before trigger: '{message_without_trigger}'")
                send_text_message(to, message_without_trigger)
            
            send_to_webhook(to, detected_trigger)
            logging.info(f"✅ Successfully sent trigger word '{detected_trigger}' to webhook.")

            return True  # ✅ Exit here so we don't request another bot reply

    # ✅ If No Trigger is Found, Send Message Normally
    return send_text_message(to, message_clean)


# 🚀 **Function to Send a Normal Text Message**
def send_text_message(to, text):
    """
    Sends a normal WhatsApp text message via WhatsApp API.
    """
    start_time = time.time()

    if not text.strip():
        logging.warning("⚠️ Attempted to send an empty message. Skipping WhatsApp request.")
        return False

    PHONE_NUMBER_ID = os.getenv("META_PHONE_NUMBER_ID")
    ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN")
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"

    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }

    #logging.info(f"📨 Sending WhatsApp Message: {payload}")

    # ✅ Attempt to Send WhatsApp Message with Retries
    for attempt in range(3):
        try:
            response = requests.post(url, headers=headers, json=payload)
            response.raise_for_status()

            execution_time = time.time() - start_time
            logging.info(f"✅ WhatsApp message sent to {to} in {execution_time:.2f} seconds on attempt {attempt + 1}")
            logging.debug(f"📨 WhatsApp API Response: {response.json()}")
            return True

        except requests.RequestException as e:
            logging.warning(f"❌ WhatsApp API Error on attempt {attempt + 1}: {e}")
            time.sleep(2)

    logging.error(f"❌ Failed to send WhatsApp message to {to} after 3 attempts.")
    return False



def send_to_webhook(from_number, message):
    """
    Sends a detected trigger word to the webhook instead of WhatsApp, ensuring it matches a valid WhatsApp webhook format.
    """

    webhook_url = os.getenv("WEBHOOK_URL")  # Store webhook URL in .env

    # Generate a valid WhatsApp-like message ID
    message_id = f"wamid.{uuid.uuid4().hex[:32]}"

    # Get current timestamp
    current_timestamp = str(int(time.time()))  # Generate a Unix timestamp

    # ✅ Adjust payload to match WhatsApp's actual webhook structure
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "3998513970398453",  # Placeholder entry ID
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "contacts": [
                                {
                                    "profile": {
                                        "name": "APEX Bot"  # Set a default sender profile
                                    },
                                    "wa_id": from_number
                                }
                            ],
                            "messages": [
                                {
                                    "from": from_number,
                                    "id": message_id,  # Assign unique message ID
                                    "text": {"body": message},
                                    "timestamp": current_timestamp,  # Add a valid timestamp
                                    "type": "text"
                                }
                            ],
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "263780556211",
                                "phone_number_id": "577823865414380"
                            }
                        }
                    }
                ]
            }
        ]
    }

    headers = {
        "Content-Type": "application/json"
    }

    logging.info(f"📡 Sending trigger word '{message}' from 12345678 to webhook: {webhook_url}")
    logging.info(f"📦 Webhook Payload: {json.dumps(payload, indent=2)}")  # Log the actual payload for debugging

    try:
        response = requests.post(webhook_url, json=payload, headers=headers)
        response.raise_for_status()  # Raises an error if response is not 200
        logging.info(f"✅ Trigger word '{message}' sent to webhook successfully.")
    except requests.RequestException as e:
        logging.error(f"❌ Webhook request failed: {e}")



def generate_ref_number():
    """
    Generates a unique incremental reference number from the database.
    Format: ATTMMDDXXX (e.g., ATT031201)
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # ✅ Get the latest reference number from the database
    cursor.execute("SELECT ref_number FROM services ORDER BY id DESC LIMIT 1;")
    last_ref = cursor.fetchone()

    # ✅ Extract numeric part and increment
    current_date = datetime.now()
    month = current_date.strftime("%m")
    day = current_date.strftime("%d")

    if last_ref:
        last_number = int(last_ref[0][-3:]) + 1  # Extract last 3 digits and increment
    else:
        last_number = 101  # Start from 101 if no previous entries exist

    ref_number = f"ATT{month}{day}{last_number:03d}"  # Format: ATTMMDDXXX

    cursor.close()
    conn.close()

    return ref_number

def extract_service_type(text):
    """
    Extracts the service type (Towing, Travel, Rental, Other) from OpenAI's response.
    """
    service_types = ["Towing", "Travel", "Rental", "Tracking", "Other"]
    
    for service in service_types:
        if service.lower() in text.lower():
            logging.info(f"Service request Detected: {service}")
            return service
    return "Other"  # Default if no service type is detected

def user_exists(from_number):
    """Checks if a user exists in the services table."""
    conn = get_db_connection()
    if not conn:
        logging.error(f"❌ Database connection failed for user {from_number}.")
        return False  # Assume user doesn't exist if DB connection fails

    try:
        cursor = conn.cursor()
        logging.info(f"🔍 Checking if user {from_number} exists in the services table...")

        # ✅ Check Services Table
        cursor.execute("SELECT 1 FROM services WHERE wa_id = %s LIMIT 1;", (from_number,))
        if cursor.fetchone():
            logging.info(f"✅ User {from_number} found in services table.")
            return True  # User exists

        logging.warning(f"⚠️ User {from_number} not found in the services table.")
        return False  # User does not exist

    except psycopg2.Error as e:
        logging.error(f"❌ Database error while checking user {from_number}: {e.pgcode} - {e.pgerror}")
        return False  # Assume user doesn't exist if an error occurs
    finally:
        cursor.close()
        conn.close()
        logging.info(f"🔌 Database connection closed for user {from_number}.")

# ✅ Temporary storage for user-thread mapping
user_threads = {}
rental_sessions = {}
drive_links = {}

def set_user_thread(from_number, thread_id):
    """Stores the OpenAI thread ID for a specific user."""
    user_threads[from_number] = thread_id
    #print(f"✅ Thread ID {thread_id} set for user {from_number}")


def process_uploaded_media(message):
    """
    Handles uploaded images by:
    1️⃣ Fetching the media URL from WhatsApp
    2️⃣ Sending the image to OpenAI in the CURRENT conversation
    3️⃣ Uploading the image to Google Drive (only after AI processing)
    4️⃣ Sending the response to the user
    """
    from_number = message.get("from", "")
    media_id = message.get("media_id")

    # ✅ Ensure media_id is valid
    if not media_id:
        logging.error("❌ No media_id found in the message.")
        return jsonify({"error": "No media found. Please upload a valid image."}), 400

    logging.info(f"📸 Processing image upload from {from_number} - Media ID: {media_id}")

    # ✅ Fetch media URL from WhatsApp
    media_url = fetch_media_url(media_id)
    
    if not media_url:
        logging.error("❌ Failed to fetch media URL from WhatsApp.")
        return jsonify({"error": "Failed to retrieve image from WhatsApp."}), 500

    logging.info(f"✅ Media URL successfully fetched: {media_url}")

    # ✅ Use existing OpenAI thread or create a new one
    thread_id = get_user_thread(from_number)
    if not thread_id:
        logging.info("🟢 No existing thread found, creating a new one...")
        thread_id = create_thread()
        if thread_id:
            set_user_thread(from_number, thread_id)
        else:
            logging.error("❌ Failed to create a new OpenAI thread.")
            return jsonify({"error": "Failed to create OpenAI thread for image analysis."}), 500

    logging.info(f"✅ Using OpenAI thread: {thread_id}")

    # ✅ Upload Image to OpenAI FIRST
    logging.info("🚀 Uploading image to OpenAI...")
    file_id = upload_file(media_url)

    if not file_id:
        logging.error("❌ Image upload to OpenAI failed.")
        send_whatsapp_message(from_number, "❌ Image upload to AI failed.", is_bot_message=True)
        return jsonify({"error": "Image upload to OpenAI failed."}), 500

    logging.info(f"✅ Image uploaded to OpenAI. File ID: {file_id}")

    # ✅ Send Image to OpenAI Thread for Analysis
    send_file_to_thread(thread_id, file_id)
    run_assistant(thread_id)

    # ✅ Get AI Response
    extracted_text = get_response(thread_id)

    # ✅ If AI processing fails, stop further processing
    if not extracted_text or "❌" in extracted_text:
        logging.error("❌ AI failed to process the image.")
        send_whatsapp_message(from_number, "❌ Image analysis failed.", is_bot_message=True)
        return jsonify({"error": "Image analysis failed"}), 500

    logging.info(f"✅ AI Extracted Text: {extracted_text}")

    # ✅ Upload Image to Google Drive AFTER OpenAI Processing
    drive_link = upload_to_google_drive(media_url, f"{from_number}_uploaded.jpg")
    drive_links[from_number] = drive_link
    if not drive_link:
        logging.error("❌ Image upload to Google Drive failed.")
        return jsonify({"error": "Image upload to Google Drive failed."}), 500

    logging.info(f"✅ Image uploaded to Google Drive: {drive_link}")

    # ✅ Send Final Response to User
    final_response = extracted_text
    send_whatsapp_message(from_number, final_response, is_bot_message=True)
    # bot_reply = query_openai_model("Send the payment trigger only if the uploaded file matches the required context (e.g., ID for car rental). Otherwise, prompt the user to upload the correct file first.",from_number)
    # # # ✅ Send Summary to Admin
    # # admin_summary = f"📢 *New Image Upload Processed!*\n📞 *Contact:* {from_number}\n📸 *Image Link:* {drive_link}\n📝 *AI Analysis:* {extracted_text}"
    # send_whatsapp_message(from_number, bot_reply)
    # rental_sessions[from_number] = admin_summary  # ✅ Store admin summary temporarily
    #trigger_payment_button(from_number)

    return jsonify({"message": "Image processed successfully."}), 200



def handle_image_upload(from_number, image_id):
    """
    Handles any image received and routes it to processing.
    """
    logging.info(f"📷 Image received from {from_number}, processing...")
    return process_uploaded_media({"from": from_number, "media_id": image_id})

def fetch_media_url(media_id):
    """
    Fetches the direct URL of a WhatsApp media file.
    """
    access_token = os.getenv("META_ACCESS_TOKEN")  # Your WhatsApp API token

    headers = {
        "Authorization": f"Bearer {access_token}",  
        "Content-Type": "application/json"
    }

    media_url = f"https://graph.facebook.com/v18.0/{media_id}"

    response = requests.get(media_url, headers=headers)

    if response.status_code == 200:
        media_data = response.json()
        media_direct_url = media_data.get("url")

        logging.info(f"✅ Media URL fetched successfully: {media_direct_url}")

        if media_direct_url:
            return media_direct_url
        else:
            logging.error("❌ Media URL is missing in response.")
            return None

    else:
        logging.error(f"❌ Failed to fetch media URL: {response.status_code} - {response.text}")
        return None

def send_whatsapp_file(recipient, file_url, file_type="image", caption=None):
    """
    Sends a file via WhatsApp with retry logic if network fails.
    """
    logging.info(f"📤 Sending file via WhatsApp to {recipient}...")

    phone_number_id = os.getenv("META_PHONE_NUMBER_ID")
    access_token = os.getenv("META_ACCESS_TOKEN")

    if not phone_number_id or not access_token:
        logging.error("❌ Missing API credentials.")
        return  

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient,
        "type": file_type,
        file_type: {
            "link": file_url
        }
    }

    if caption:
        payload[file_type]["caption"] = caption

    url = f"https://graph.facebook.com/v18.0/{phone_number_id}/messages"

    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        logging.info(f"✅ File sent successfully to {recipient}: {file_url}")

    except requests.exceptions.RequestException as e:
        logging.error(f"❌ Failed to send file to {recipient}. Retrying later.")

        # ✅ Save pending response with retry count
        save_pending_response(recipient, file_url, file_type=file_type, caption=caption, retries=1)

def process_user_selection(response_data, message):
    """
    Processes the user's service selection and sends the appropriate flyer via WhatsApp.
    """
    from_number = message.get("from", "")
    user_selection = response_data.get("user_selection")  
    logging.info(f"🔍 Processing user selection: '{user_selection}'")  # Add Debugging
    # Mapping of selections to file URLs and captions
    service_flyers = {
        "1": 
        [
            {
            "file_url": "https://drive.google.com/uc?export=download&id=1IcWv2AqwDZ0_Pq_p4rlN56JUWey3boAx",
            "caption": "🚗 Our Fuel Savers!\n"
                        "- Honda Fit, Toyota Aqua\n"
                        "- Price: $39.95 - $45/day\n"
                        "- 150km free mileage per day (cumulative)\n"
                        "- Example: 10 days = 1500km mileage\n"
                        "- Excess mileage: $0.50 per km\n"
                        "- $200 refundable security deposit required"


        },
        
         {
            "file_url": "https://drive.google.com/uc?export=download&id=19DoI3c2dTHnLMyJk5XSna_mJ3s3arTDp",
            "caption": "🚗 Our Budget Sedans!\n"
                        "- Toyota Axio, Toyota Belta\n"
                        "- Price: $50 - $80/day\n"
                        "- 150km free mileage per day (cumulative)\n"
                        "- Example: 10 days = 1500km mileage\n"
                        "- Excess mileage: $0.50 per km\n"
                        "- $200 refundable security deposit required"


        },

        {
            "file_url": "https://drive.google.com/uc?export=download&id=1uIweDBA7C9ANsKfsMjncTkxSy3Y6mfjs",
           "caption": "🚗 Our Mid-Range SUVs!\n"
                        "- Mazda CX5, Honda Vezel, Nissan X-Trail\n"
                        "- Price: from $79.95/day\n"
                        "- 150km free mileage per day (cumulative)\n"
                        "- Example: 10 days = 1500km mileage\n"
                        "- Excess mileage: $0.50 per km\n"
                        "- $200 refundable security deposit required"


        },

        {
            "file_url": "https://drive.google.com/uc?export=download&id=1fxHOsGYsuOJg4Qx1iHOOndqYZBi5v1_E",
            "caption": "🚗 Our SUVs!\n"
                        "- Toyota Fortuner, TrailBlazer\n"
                        "- Price: $200/day\n"
                        "- 150km free mileage per day (cumulative)\n"
                        "- Example: 10 days = 1500km mileage\n"
                        "- Excess mileage: $0.50 per km\n"
                        "- $200 refundable security deposit required"


        },

        {
            "file_url": "https://drive.google.com/uc?export=download&id=1y3zekPU7EncfRcp81Iq6nv46PRFQwM5M",
            "caption": "🚗 Our Double Cabs!\n"
                        "- Toyota Hilux, Toyota Hilux GD6, Double Cabs & SUVs\n"
                        "- Price: $150/day\n"
                        "- 150km free mileage per day (cumulative)\n"
                        "- Example: 10 days = 1500km mileage\n"
                        "- Excess mileage: $0.75 per km\n"
                        "- $200 refundable security deposit required"


        },

        {
            "file_url": "https://drive.google.com/uc?export=download&id=17SSdh0r_S_V2ObZ0fWqZUZ7qf5BFQc_O",
            "caption": "🚗 Our Luxury Vehicles!\n"
                        "- Mercedes E-Class, Mercedes C-Class\n"
                        "- Price: $150 - $200/day\n"
                        "- 150km free mileage per day (cumulative)\n"
                        "- Example: 10 days = 1500km mileage\n"
                        "- Excess mileage: $0.90 per km\n"
                        "- $200 refundable security deposit required"


        },
        ],
        
        "2": [
            {
            "file_url": "https://drive.google.com/uc?export=download&id=1_QN1FAbsJaLISwOsLWMkAwdSISOuImAQ",
            "caption": "✈️ Travel Services - Let us plan your next trip!\n"
                        "- If you need a bigger bus, call our Travel Agent\n"
                        "📞 +263712345678 for a quotation."

        },
        ],
        "3":[
             {
            "file_url": "https://drive.google.com/uc?export=download&id=1LJ6MTgiq1O71uYTWVMm6sk0szsmdfx91",
            "caption": "🚛 Towing Services - Reliable towing anytime, anywhere!"
        },
        ],
        "4": [
            {
            "file_url": "https://drive.google.com/uc?export=download&id=162Lbs7Lb_bQMyFlq86oMVRm_XQ4t6ezT",
            "caption": "📍 Tracking Services - Keep an eye on your Vehicles!"
        },
        ],
        "5": [
            {
            "file_url": "https://drive.google.com/uc?export=download&id=12qg43CB3oE40HcC_umlqjTbXDMsA-Y3l",
            "caption": "📦 Freight Services - We move cargo efficiently!\n"
                "- To get Freight Services, call our Freight Operator\n"
                "📞 +263712345678"

        },
        ],
    }

    # Ensure the selected option exists in `service_flyers`
    if user_selection in service_flyers:
        flyer_list = service_flyers[user_selection]  # ✅ Get the list of flyers
        
        for flyer in flyer_list:  # ✅ Loop through multiple flyers if available
            send_whatsapp_file(from_number, flyer["file_url"], file_type="image", caption=flyer["caption"])
    
    logging.warning(f"⚠️ Invalid Process User selection received: {user_selection}")
    # send_whatsapp_message(from_number, "Please enter your name on the form first")
    return {"error": "Invalid Process User selection. Please choose a valid option (1-5)."}, 400



def send_specific_car(response_data, message):
    """
    Processes the user's vehicle type selection and sends the appropriate flyer via WhatsApp.
    """
    from_number = message.get("from", "")
    user_selection = response_data.get("user_selection", "").strip().lower()   # Extract user response
    logging.info(f"🔍 Processing user selection: '{user_selection}'")  # Add Debugging

    # Mapping of selections to file URLs and captions
    specific_classes = {
        "fuel_saver": 
        [
            {
        "file_url": "https://drive.google.com/uc?export=download&id=1JvXG6FdoVNXFSNw_oCSGgOCw4XqKEds9",
        "caption": "🚗 GK3 Fit, Luxury Fuel Saver\n"
                   "- $60/day\n"
                   "- New Generation Model\n"
                   "- 150km free mileage per day (cumulative)\n"
                   "- Example: 10 days = 1500km mileage\n"
                   "- $200 refundable security deposit"
    },

    {
        "file_url": "https://drive.google.com/uc?export=download&id=16fm8noE0rQpfHV9drjNKMqpMOhMKklBQ",
        "caption": "🚗 Axio, Sedan Fuel Saver\n"
                   "- $65/day\n"
                   "- New Generation Model\n"
                   "- 150km free mileage per day (cumulative)\n"
                   "- Example: 10 days = 1500km mileage\n"
                   "- $200 refundable security deposit"
    },

    {
        "file_url": "https://drive.google.com/uc?export=download&id=1UJk-i9t9slFp_GgTbtWJ3N3F7qRCw0E_",
        "caption": "🚗 GE6 Fit, Budget Fuel Saver\n"
                   "- $40/day\n"
                   "- 150km free mileage per day (cumulative)\n"
                   "- Example: 10 days = 1500km mileage\n"
                   "- $200 refundable security deposit"
    },

    {
        "file_url": "https://drive.google.com/uc?export=download&id=1KpM8EUcyq4mPxyT3wq7O60TT8hBF1tPr",
        "caption": "🚗 Toyota Aqua, Hybrid Fuel Saver\n"
                   "- $45/day\n"
                   "- 150km free mileage per day (cumulative)\n"
                   "- Example: 10 days = 1500km mileage\n"
                   "- $200 refundable security deposit"
    }
        ],
        "mid_suv": 
        [
            {
        "file_url": "https://drive.google.com/uc?export=download&id=17QzuSOdvVWifWQFpBhlsGk7LU_n6-V0-",
        "caption": "🚗 Nissan X-Trail (Mid-Range SUV)\n"
                   "- $79.95/day\n"
                   "- 150km free mileage per day (cumulative)\n"
                   "- Example: 10 days = 1500km mileage\n"
                   "- $200 refundable security deposit"
    },

    {
        "file_url": "https://drive.google.com/uc?export=download&id=1uIweDBA7C9ANsKfsMjncTkxSy3Y6mfjs",
        "caption": "🚗 Mazda CX-5 (Mid-Range SUV)\n"
                   "- $79.95/day\n"
                   "- 150km free mileage per day (cumulative)\n"
                   "- Example: 10 days = 1500km mileage\n"
                   "- $200 refundable security deposit"
    }
        ],
        "suv": 
        [
            {
        "file_url": "https://drive.google.com/uc?export=download&id=1fxHOsGYsuOJg4Qx1iHOOndqYZBi5v1_E",
        "caption": "🚗 GD6 Fortuner\n"
                   "- $250/day\n"
                   "- 200km free mileage per day (cumulative)\n"
                   "- Example: 10 days = 2000km mileage\n"
                   "- $200 refundable security deposit"
    },

    {
        "file_url": "https://drive.google.com/uc?export=download&id=1Dd_rvsiv02mwrlTh5oskp859LRW8wmc9",
        "caption": "🚗 Chevrolet Trailblazer\n"
                   "- $150/day\n"
                   "- 200km free mileage per day (cumulative)\n"
                   "- Example: 10 days = 2000km mileage\n"
                   "- $200 refundable security deposit"
    }
        ],
        "double_cab": 
        [
            {
        "file_url": "https://drive.google.com/uc?export=download&id=1y3zekPU7EncfRcp81Iq6nv46PRFQwM5M",
        "caption": "🚗 D4D Hilux\n"
                   "- $150/day\n"
                   "- 200km free mileage per day (cumulative)\n"
                   "- Example: 10 days = 2000km mileage\n"
                   "- $200 refundable security deposit"
    },

    {
        "file_url": "https://drive.google.com/uc?export=download&id=1PAg9WgC2QixgDND0A3udNnZi7Q1RBVHx",
        "caption": "🚗 Ford Ranger T7\n"
                   "- $150/day\n"
                   "- 200km free mileage per day (cumulative)\n"
                   "- Example: 10 days = 2000km mileage\n"
                   "- $200 refundable security deposit"
    },

    {
        "file_url": "https://drive.google.com/uc?export=download&id=1xWLJypyP7kWvtaQIesF-OvEOnjrBacka",
        "caption": "🚗 Nissan Navara (Automatic)\n"
                   "- $250/day\n"
                   "- 200km free mileage per day (cumulative)\n"
                   "- Example: 10 days = 2000km mileage\n"
                   "- $200 refundable security deposit"
    }
        ],
        "mini_buses": 
        [{
        "file_url": "https://drive.google.com/uc?export=download&id=12DXS_CabhLSpjUHv35-IrrN_ri2ldHxo",
        "caption": "🚗 Nissan Minibus NV350 (9-Seater)\n"
                   "- $150/day\n"
                   "- 200km free mileage per day (cumulative)\n"
                   "- Example: 10 days = 2000km mileage\n"
                   "- $200 refundable security deposit"
    },

    {
        "file_url": "https://drive.google.com/uc?export=download&id=1KTayO63f722eDJcLNjxACemWZFCZ7OxY",
        "caption": "🚗 Nissan Minibus NV350 (13-Seater)\n"
                   "- $250/day\n"
                   "- 200km free mileage per day (cumulative)\n"
                   "- Example: 10 days = 2000km mileage\n"
                   "- $200 refundable security deposit"
    }
        ],
        "luxury": 
        [
             {
        "file_url": "https://drive.google.com/uc?export=download&id=1L-7rUoiLSsFmI5b1y6PYGLQnP423ZPrO",
        "caption": "🚗 Mercedes Benz C-Class 204 (Facelift)\n"
                   "- $150/day\n"
                   "- 150km free mileage per day (cumulative)\n"
                   "- Example: 10 days = 1500km mileage\n"
                   "- $200 refundable security deposit"
    },

    {
        "file_url": "https://drive.google.com/uc?export=download&id=1gppFtETReo4Jc5ehdjExuFOArnfhvvyB",
        "caption": "🚗 Mercedes Benz E-Class 212 (Facelift)\n"
                   "- $200/day\n"
                   "- 150km free mileage per day (cumulative)\n"
                   "- Example: 10 days = 1500km mileage\n"
                   "- $200 refundable security deposit"
    }
        ]
    }

    # Ensure the selected option exists in service_flyers
    if user_selection in specific_classes:
        flyer_list = specific_classes[user_selection]  # ✅ Get the list of flyers
        sent_message = f"✅ Here are the details for {user_selection.upper()} category:\n"

        for flyer in flyer_list:  # ✅ Loop through multiple flyers
            file_url = flyer["file_url"]
            caption = flyer["caption"]

            # Send each flyer
            send_whatsapp_file(from_number, file_url, file_type="image", caption=caption)
            sent_message += f"\n{flyer['caption']}"

        return {"message": f"Flyers for option {user_selection} sent successfully."}, 200

    logging.warning(f"⚠ Invalid  Send Specific Car selection received: {user_selection}")
    return {"error": "Invalid selection. Please choose a valid option (1-5)."}, 400

def send_car_model(response_data, message):
    """
    Sends the specific car rental model based on the user's selection.
    """
    from_number = message.get("from", "")
    car_category = response_data.get("user_selection", "").strip().lower()  # Extract user response

    # Mapping of car categories to flyer URLs and captions
    car_models = {
        
        "all_car_images": [
            {"file_url": "https://drive.google.com/uc?export=download&id=1UJk-i9t9slFp_GgTbtWJ3N3F7qRCw0E_",
              "caption": "Honda Fit RS\n"
                   "- $40/day\n"
                   "- 150km free mileage per day (cumulative)\n"
                   "- Example: 10 days = 1500km mileage\n"
                   "- $200 refundable security deposit"},
            {"file_url": "https://drive.google.com/uc?export=download&id=1IcWv2AqwDZ0_Pq_p4rlN56JUWey3boAx", 
            "caption": ""},
            {"file_url": "https://drive.google.com/uc?export=download&id=1ewwSs1ACSmX3rVJGZytDSpF-qqeJII0Q",
             "caption": "Honda Fit GK3\n"
                   "- $60/day\n"
                   "- 150km free mileage per day (cumulative)\n"
                   "- Example: 10 days = 1500km mileage\n"
                   "- $200 refundable security deposit"},
            {"file_url": "https://drive.google.com/uc?export=download&id=1JvXG6FdoVNXFSNw_oCSGgOCw4XqKEds9", 
            "caption": ""},
            {"file_url": "https://drive.google.com/uc?export=download&id=1KpM8EUcyq4mPxyT3wq7O60TT8hBF1tPr",
             "caption": ""},
            {"file_url": "https://drive.google.com/uc?export=download&id=1JUhBV0rwyll6xhNcyiwNlgn3Ox_-36WP", 
            "caption": "Toyota Aqua\n"
                   "- $45/day\n"
                   "- 150km free mileage per day (cumulative)\n"
                   "- Example: 10 days = 1500km mileage\n"
                   "- $200 refundable security deposit"},
            {"file_url": "https://drive.google.com/uc?export=download&id=1nJmfdT0_n1tGwqUV1ZDVCsG8G0s-K58f",
             "caption": "7"},
            {"file_url": "https://drive.google.com/uc?export=download&id=1hb-0zZ5toK8xw8HzMZYj_t6EoaDuP2Vy", 
            "caption": "Honda Vezel  \n"
                   "- $80/day\n"
                   "- 150km free mileage per day (cumulative)\n"
                   "- Example: 10 days = 1500km mileage\n"
                   "- $200 refundable security deposit"},
            {"file_url": "https://drive.google.com/uc?export=download&id=1uIweDBA7C9ANsKfsMjncTkxSy3Y6mfjs", 
            "caption": "Mazda CX5 \n"
                   "- $800/day\n"
                   "- 150km free mileage per day (cumulative)\n"
                   "- Example: 10 days = 2000km mileage\n"
                   "- $200 refundable security deposit"},
            {"file_url": "https://drive.google.com/uc?export=download&id=1_uWVWPmJCne8oooLkJEpF1Jcsx33l7Pp",
             "caption": ""},
            {"file_url": "https://drive.google.com/uc?export=download&id=17QzuSOdvVWifWQFpBhlsGk7LU_n6-V0-",
             "caption": "Nissan Qashqai\n"
                   "- $80/day\n"
                   "- 150km free mileage per day (cumulative)\n"
                   "- Example: 10 days = 1500km mileage\n"
                   "- $200 refundable security deposit"},
            {"file_url": "https://drive.google.com/uc?export=download&id=1vcQMuqgzAxAUYDF3ToI4E15B3RlmAseZ", 
            "caption": "12"},
            {"file_url": "https://drive.google.com/uc?export=download&id=1Dd_rvsiv02mwrlTh5oskp859LRW8wmc9",
             "caption": "Chevrolet TrailBlazer\n"
                   "- $150/day\n"
                   "- 200km free mileage per day (cumulative)\n"
                   "- Example: 10 days = 2000km mileage\n"
                   "- $200 refundable security deposit"},
            {"file_url": "https://drive.google.com/uc?export=download&id=1piNTEhvruTHQzHkSm9Zj9DXrQrMsXkVE", 
            "caption": "Toyota LandCruiser 300 Series\n"
                   "- $750/day\n"
                   "- 200km free mileage per day (cumulative)\n"
                   "- Example: 10 days = 2000km mileage\n"
                   "- $200 refundable security deposit"},
            {"file_url": "https://drive.google.com/uc?export=download&id=1fxHOsGYsuOJg4Qx1iHOOndqYZBi5v1_E",
             "caption": "Toyota Fortuner GD6\n"
                   "- $250/day\n"
                   "- 200km free mileage per day (cumulative)\n"
                   "- Example: 10 days = 2000km mileage\n"
                   "- $200 refundable security deposit"},
            {"file_url": "https://drive.google.com/uc?export=download&id=1VLaBUul4oWtWMw-Nr9jJJMLbv7MXK5xO",
             "caption": "Toyota Prado\n"
                   "- $600/day\n"
                   "- 200km free mileage per day (cumulative)\n"
                   "- Example: 10 days = 2000km mileage\n"
                   "- $200 refundable security deposit"},
            {"file_url": "https://drive.google.com/uc?export=download&id=1xWLJypyP7kWvtaQIesF-OvEOnjrBacka",
             "caption": "Nissan Navara\n"
                   "- $250/day\n"
                   "- 150km free mileage per day (cumulative)\n"
                   "- Example: 10 days = 1500km mileage\n"
                   "- $200 refundable security deposit"},
            {"file_url": "https://drive.google.com/uc?export=download&id=1gzwY7WJkgfNHf8D1WIT8e10kEHQzzw0M", 
            "caption": "18"},
            {"file_url": "https://drive.google.com/uc?export=download&id=1y3zekPU7EncfRcp81Iq6nv46PRFQwM5M", 
             "caption": "Toyota Hlux D4D\n"
                   "- $200/day\n"
                   "- 200km free mileage per day (cumulative)\n"
                   "- Example: 10 days = 2000km mileage\n"
                   "- $200 refundable security deposit"},
            {"file_url": "https://drive.google.com/uc?export=download&id=1PAg9WgC2QixgDND0A3udNnZi7Q1RBVHx",
             "caption": "Ford Ranger \n"
                   "- $150/day\n"
                   "- 200km free mileage per day (cumulative)\n"
                   "- Example: 10 days = 2000km mileage\n"
                   "- $200 refundable security deposit"},
            {"file_url": "https://drive.google.com/uc?export=download&id=1L-7rUoiLSsFmI5b1y6PYGLQnP423ZPrO",
            "caption": "Mercedes Benz C-Class 204 (Facelift)\n"
                   "- $150/day\n"
                   "- 200km free mileage per day (cumulative)\n"
                   "- Example: 10 days = 2000km mileage\n"
                   "- $200 refundable security deposit"},
            {"file_url": "https://drive.google.com/uc?export=download&id=1gppFtETReo4Jc5ehdjExuFOArnfhvvyB",
             "caption": "Mercedes Benz E-Class 212 (Facelift) \n"
                   "- $200/day\n"
                   "- 200km free mileage per day (cumulative)\n"
                   "- Example: 10 days = 2000km mileage\n"
                   "- $200 refundable security deposit"},
            {"file_url": "https://drive.google.com/uc?export=download&id=16fm8noE0rQpfHV9drjNKMqpMOhMKklBQ", 
            "caption": "Toyota Axio\n"
                   "- $65/day\n"
                   "- 150km free mileage per day (cumulative)\n"
                   "- Example: 10 days = 1500km mileage\n"
                   "- $200 refundable security deposit"},
            {"file_url": "https://drive.google.com/uc?export=download&id=1KTayO63f722eDJcLNjxACemWZFCZ7OxY",
             "caption": "NV350 13 Seater\n"
                   "- $250/day\n"
                   "- 200km free mileage per day (cumulative)\n"
                   "- Example: 10 days = 2000km mileage\n"
                   "- $200 refundable security deposit"},
             {
                "file_url": "https://drive.google.com/uc?export=download&id=1IIGGZ5A8xyc6sB-WMIW6zn1JrDIy7kAM",
                "caption": "Toyota GD6 Hilux!\n"
                "-$250/day\n" 
                 "- 200km free mileage per day (cumulative)\n"
                   "- Example: 10 days = 2000km mileage\n"
                   "- $200 refundable security deposit"}
             
        ],
        "honda_fits": [
            {
                "file_url": "https://drive.google.com/uc?export=download&id=1UJk-i9t9slFp_GgTbtWJ3N3F7qRCw0E_",
                "caption": "🚗Honda Fit Rs! PRICE: $40 per day with cumulative 150km mileage. Deposit $200"
            },

            {
                "file_url": "https://drive.google.com/uc?export=download&id=1ewwSs1ACSmX3rVJGZytDSpF-qqeJII0Q",
                "caption": "🚗Honda Fit GK3! PRICE: $60 per day with cumulative 150km mileage. Deposit $200"
        }],
        "honda_fit_rs": [
            {
                "file_url": "https://drive.google.com/uc?export=download&id=1UJk-i9t9slFp_GgTbtWJ3N3F7qRCw0E_",
                "caption": "Honda Fit RS\n"
                        "- $40/day\n"
                        "- 150km free mileage per day (cumulative)\n"
                        "- Example: 10 days = 1500km mileage\n"
                        "- $200 refundable security deposit"
            },
            {
                "file_url": "https://drive.google.com/uc?export=download&id=1IcWv2AqwDZ0_Pq_p4rlN56JUWey3boAx",
                "caption": "Honda Fit RS (Additional View)"
            }
        ],

        "honda_fit_gk3": [
            {
                "file_url": "https://drive.google.com/uc?export=download&id=1ewwSs1ACSmX3rVJGZytDSpF-qqeJII0Q",
                "caption": "Honda Fit GK3\n"
                        "- $60/day\n"
                        "- 150km free mileage per day (cumulative)\n"
                        "- Example: 10 days = 1500km mileage\n"
                        "- $200 refundable security deposit"
            },
            {
                "file_url": "https://drive.google.com/uc?export=download&id=1JvXG6FdoVNXFSNw_oCSGgOCw4XqKEds9",
                "caption": "Honda Fit GK3 (Additional View)"
            }
        ],

    
"toyota_aqua": [
    {
        "file_url": "https://drive.google.com/uc?export=download&id=1KpM8EUcyq4mPxyT3wq7O60TT8hBF1tPr",
        "caption": "Toyota Aqua\n"
                   "- $45/day\n"
                   "- 150km free mileage per day (cumulative)\n"
                   "- Example: 10 days = 1500km mileage\n"
                   "- $200 refundable security deposit"
    },
    {
        "file_url": "https://drive.google.com/uc?export=download&id=1JUhBV0rwyll6xhNcyiwNlgn3Ox_-36WP",
        "caption": "Toyota Aqua (Additional View)"
    }
],

"honda_vezel": [
    {
        "file_url": "https://drive.google.com/uc?export=download&id=1hb-0zZ5toK8xw8HzMZYj_t6EoaDuP2Vy",
        "caption": "Honda Vezel\n"
                   "- $80/day\n"
                   "- 150km free mileage per day (cumulative)\n"
                   "- Example: 10 days = 1500km mileage\n"
                   "- $200 refundable security deposit"
    }
],

"mazda_cx5": [
    {
        "file_url": "https://drive.google.com/uc?export=download&id=1uIweDBA7C9ANsKfsMjncTkxSy3Y6mfjs",
        "caption": "Mazda CX5\n"
                   "- $80/day\n"
                   "- 150km free mileage per day (cumulative)\n"
                   "- Example: 10 days = 1500km mileage\n"
                   "- $200 refundable security deposit"
    },
    {
        "file_url": "https://drive.google.com/uc?export=download&id=1_uWVWPmJCne8oooLkJEpF1Jcsx33l7Pp",
        "caption": "Mazda CX5 (Additional View)"
    }
],

"x-trail": [
    {
        "file_url": "https://drive.google.com/uc?export=download&id=1N7nRkhLTMZ1tesXKpdVKGczMSVbFyFWZ",
        "caption": "Nissan X-Trail\n"
                   "- $80/day\n"
                   "- 150km free mileage per day (cumulative)\n"
                   "- Example: 10 days = 1500km mileage\n"
                   "- $200 refundable security deposit"
    },
    {
        "file_url": "https://drive.google.com/uc?export=download&id=1vcQMuqgzAxAUYDF3ToI4E15B3RlmAseZ",
        "caption": "Nissan X-Trail (Additional View)"
    }
],

"trail_blazer": [
    {
        "file_url": "https://drive.google.com/uc?export=download&id=1Dd_rvsiv02mwrlTh5oskp859LRW8wmc9",
        "caption": "Chevrolet Trail Blazer\n"
                   "- $200/day\n"
                   "- 200km free mileage per day (cumulative)\n"
                   "- Example: 10 days = 2000km mileage\n"
                   "- $200 refundable security deposit"
    }
],

"toyota_landcruiser_300_series": [
    {
        "file_url": "https://drive.google.com/uc?export=download&id=1piNTEhvruTHQzHkSm9Zj9DXrQrMsXkVE",
        "caption": "Toyota Landcruiser 300 Series\n"
                   "- $750/day\n"
                   "- 200km free mileage per day (cumulative)\n"
                   "- Example: 10 days = 2000km mileage\n"
                   "- $200 refundable security deposit"
    }
],

"gd6_hilux": [
    {
        "file_url": "https://drive.google.com/uc?export=download&id=1IIGGZ5A8xyc6sB-WMIW6zn1JrDIy7kAM",
        "caption": "Toyota GD6 Hilux\n"
                   "- $250/day\n"
                   "- 200km free mileage per day (cumulative)\n"
                   "- Example: 10 days = 2000km mileage\n"
                   "- $200 refundable security deposit"
    }
],

    "trigger_toyota_fortuner": [
        {
            "file_url": "https://drive.google.com/uc?export=download&id=1fxHOsGYsuOJg4Qx1iHOOndqYZBi5v1_E",
            "caption": "Toyota Fortuner GD6\n"
                    "- $200/day\n"
                    "- 200km free mileage per day (cumulative)\n"
                    "- Example: 10 days = 2000km mileage\n"
                    "- $200 refundable security deposit"
        },
        {
            "file_url": "https://drive.google.com/uc?export=download&id=1IIGGZ5A8xyc6sB-WMIW6zn1JrDIy7kAM",
            "caption": "Toyota Fortuner GD6 (Additional View)"
        }
    ],

    "toyota_prado": [
        {
            "file_url": "https://drive.google.com/uc?export=download&id=1VLaBUul4oWtWMw-Nr9jJJMLbv7MXK5xO",
            "caption": "Toyota Prado\n"
                    "- $650/day\n"
                    "- 200km free mileage per day (cumulative)\n"
                    "- Example: 10 days = 2000km mileage\n"
                    "- $200 refundable security deposit"
        }
    ],

    "nissan_navara": [
        {
            "file_url": "https://drive.google.com/uc?export=download&id=1xWLJypyP7kWvtaQIesF-OvEOnjrBacka",
            "caption": "Nissan Navara\n"
                    "- $250/day\n"
                    "- 150km free mileage per day (cumulative)\n"
                    "- Example: 10 days = 1500km mileage\n"
                    "- $200 refundable security deposit"
        },
        {
            "file_url": "https://drive.google.com/uc?export=download&id=1gzwY7WJkgfNHf8D1WIT8e10kEHQzzw0M",
            "caption": "Nissan Navara (Additional View)"
        }
],"toyota_hilux_d4d": [
    {
        "file_url": "https://drive.google.com/uc?export=download&id=1y3zekPU7EncfRcp81Iq6nv46PRFQwM5M",
        "caption": "Toyota Hilux D4D\n"
                   "- $200/day\n"
                   "- 200km free mileage per day (cumulative)\n"
                   "- Example: 10 days = 2000km mileage\n"
                   "- $200 refundable security deposit"
    }
],

"ford_ranger": [
    {
        "file_url": "https://drive.google.com/uc?export=download&id=1PAg9WgC2QixgDND0A3udNnZi7Q1RBVHx",
        "caption": "Ford Ranger\n"
                   "- $150/day\n"
                   "- 200km free mileage per day (cumulative)\n"
                   "- Example: 10 days = 2000km mileage\n"
                   "- $200 refundable security deposit"
    }
],

"isuzu_x-rider": [
    {
        "file_url": "https://drive.google.com/uc?export=download&id=1I6hmK1B3SR1wtTdAgAvXj2VH6yHMSYov",
        "caption": "Isuzu X-Rider\n"
                   "- $150/day\n"
                   "- 200km free mileage per day (cumulative)\n"
                   "- Example: 10 days = 2000km mileage\n"
                   "- $200 refundable security deposit"
    }
],

"axio": [
    {
        "file_url": "https://drive.google.com/uc?export=download&id=16fm8noE0rQpfHV9drjNKMqpMOhMKklBQ",
        "caption": "Toyota Axio (Sedan Fuel Saver)\n"
                   "- $65/day\n"
                   "- 150km free mileage per day (cumulative)\n"
                   "- Example: 10 days = 1500km mileage\n"
                   "- $200 refundable security deposit"
    }
],

"nv350_9_seater": [
    {
        "file_url": "https://drive.google.com/uc?export=download&id=1KTayO63f722eDJcLNjxACemWZFCZ7OxY",
        "caption": "Nissan NV350 9 Seater\n"
                   "- $150/day\n"
                   "- 200km free mileage per day (cumulative)\n"
                   "- Example: 10 days = 2000km mileage\n"
                   "- $200 refundable security deposit"
    }
],

"nv350_13_seater": [
    {
        "file_url": "https://drive.google.com/uc?export=download&id=1KTayO63f722eDJcLNjxACemWZFCZ7OxY",
        "caption": "Nissan NV350 13 Seater\n"
                   "- $250/day\n"
                   "- 200km free mileage per day (cumulative)\n"
                   "- Example: 10 days = 2000km mileage\n"
                   "- $200 refundable security deposit"
    }
],

"mercedes_benz_e_class": [
    {
        "file_url": "https://drive.google.com/uc?export=download&id=1gppFtETReo4Jc5ehdjExuFOArnfhvvyB",
        "caption": "Mercedes Benz E-Class 212 (Facelift)\n"
                   "- $200/day\n"
                   "- 150km free mileage per day (cumulative)\n"
                   "- Example: 10 days = 1500km mileage\n"
                   "- $200 refundable security deposit"
    }
],

"mercedes_benz_c_class": [
    {
        "file_url": "https://drive.google.com/uc?export=download&id=1L-7rUoiLSsFmI5b1y6PYGLQnP423ZPrO",
        "caption": "Mercedes Benz C-Class 204 (Facelift)\n"
                   "- $150/day\n"
                   "- 150km free mileage per day (cumulative)\n"
                   "- Example: 10 days = 1500km mileage\n"
                   "- $200 refundable security deposit"
    }
]
    }

    # Check if the category exists and send images
    sent_message = ""
    if car_category in car_models:
        for pic in car_models[car_category]:  # ✅ Loop through multiple flyers
            send_whatsapp_file(from_number, pic["file_url"], file_type="image", caption=pic["caption"])
            sent_message  += f"\n{pic['caption']}"
        return {"message": f"Flyer(s) for {car_category} sent successfully."}, 200

    logging.warning(f"⚠ Invalid car category received: {car_category}")
    return {"error": "Invalid category. Please choose a valid option."}, 400



def is_duplicate_message(message_id):
    """
    Checks if a message has already been processed using a database.
    """
    conn = get_db_connection()
    if not conn:
        return False  # If DB fails, assume message is new (to avoid blocking processing)

    try:
        cursor = conn.cursor()

        # ✅ Check if message_id exists in the database
        cursor.execute("SELECT EXISTS(SELECT 1 FROM processed_messages WHERE message_id = %s)", (message_id,))
        exists = cursor.fetchone()[0]

        if exists:
            return True  # ✅ Duplicate detected

        # ✅ If not exists, insert message_id into the database
        cursor.execute("INSERT INTO processed_messages (message_id) VALUES (%s)", (message_id,))
        conn.commit()
        return False  # ✅ Message is new, process it

    except psycopg2.Error as e:
        logging.error(f"❌ Database error while checking duplicate messages: {e}")
        return False  # If DB error, assume message is new to prevent blocking

    finally:
        cursor.close()
        conn.close()

def save_location(from_number, lat, lon):
    """Save the extracted location details to the database."""
    maps_link = f"https://www.google.com/maps?q={lat},{lon}"
    location_text = f"Latitude: {lat}, Longitude: {lon}"

    conn = get_db_connection()
    if not conn:
        return "❌ Database connection failed."

    try:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO locations (contact_number, latitude, longitude, google_maps_link, location_text, created_at)
            VALUES (%s, %s, %s, %s, %s, NOW())
        ''', (from_number, lat, lon, maps_link, location_text))

        conn.commit()
        return f"✅ Location saved: {location_text}"

    except psycopg2.Error as e:
        logging.error(f"❌ Database error while saving location: {e}")
        return "❌ Error saving location."

    finally:
        cursor.close()
        conn.close()


# Dictionary to store the last 4 received messages per user
user_message_history = {}
   
@app.route('/webhook', methods=['GET', 'POST'])
def whatsapp_webhook():
    """
    Handles WhatsApp Webhook Verification (GET request) and Incoming Messages (POST request).
    """

    #def whatsapp_webhook():
    """
    Handle GET requests for webhook verification and POST requests for incoming messages.
    """
    
    if request.method == 'GET':
        verify_token = os.getenv('VERIFY_TOKEN')
        hub_mode = request.args.get('hub.mode')
        hub_token = request.args.get('hub.verify_token')
        hub_challenge = request.args.get('hub.challenge')

        logging.info(f"Received verification: mode={hub_mode}, token={hub_token}")

        if not all([hub_mode, hub_token, hub_challenge]):
            logging.error("Missing parameters in GET request")
            return "Bad Request", 400

        if hub_mode == 'subscribe' and hub_token == verify_token:
            logging.info("Webhook verified successfully")
            return hub_challenge, 200
        else:
            logging.error(f"Verification failed. Expected token={verify_token}, got {hub_token}")
            return "Forbidden", 403
        
    # ✅ WhatsApp Message Handling
    elif request.method == 'POST':
        meta_phone_number_id = os.getenv('META_PHONE_NUMBER_ID')  # Ensure the environment variable is set
        #meta_access_token = os.getenv('META_ACCESS_TOKEN')  # Ensure the environment variable is set
        url = f"https://graph.facebook.com/v13.0/{meta_phone_number_id}/messages"

        try:
            data = request.json
            if not data:
                logging.warning("❌ No data received in request.")
                return jsonify({"error": "No data received"}), 400
            
            logging.info(f"📩 Incoming Webhook Data: {json.dumps(data, indent=2)}")

            entry = data.get('entry', [{}])[0]
            changes = entry.get('changes', [{}])[0]
            value = changes.get('value', {})

            # ✅ Handle WhatsApp Status Updates
            if 'statuses' in value:
                logging.info("📩 Received WhatsApp message status update. No action needed.")
                return jsonify({"message": "Status update received"}), 200

            # ✅ Check if there are messages
            if 'messages' not in value:
                logging.warning("❌ No messages found in webhook event.")
                return jsonify({"error": "No messages found"}), 400
            
            if "messages" in data.get("entry", [{}])[0].get("changes", [{}])[0].get("value", {}):
                logging.info("📩 Received new WhatsApp message. Processing...")
            elif "statuses" in data.get("entry", [{}])[0].get("changes", [{}])[0].get("value", {}):
                logging.debug("📩 Received WhatsApp message status update. Ignoring...")
                return jsonify({"message": "Status update received"}), 200  # ✅ Skip logging status updates

            message = value['messages'][0]
            from_number = message['from']
            message_id = message.get('id')

            if message.get("type") == "location":
                lat = message["location"]["latitude"]
                lon = message["location"]["longitude"]

                # Generate a Google Maps link
                maps_link = f"https://www.google.com/maps?q={lat},{lon}"

                # Save location in DB
                save_location(from_number, lat, lon)

                # Instead of sending an empty message to OpenAI, send a meaningful message
                bot_reply = f"User has sent their GPS location, : {maps_link}.\n"
                "(If its the destination location provide an estimate towing fee to come and tow them from pickup to their destination place using the given formular)"
                bot_reply = query_openai_model(bot_reply, from_number)
                
                send_whatsapp_message(from_number, bot_reply, is_bot_message=True)
                return jsonify({"message": "Location received and processed."}), 200  # ✅ STOP further processing
            

             # ✅ Prevent duplicate processing
            if is_duplicate_message(message_id):
                logging.info(f"🔄 Duplicate message detected: {message_id}. Ignoring...")
                return jsonify({"message": "Duplicate message ignored"}), 200
               # processed_messages.add(message_id)  # ✅ Mark this message as processed

            # ✅ Ignore old messages (older than 5 minutes)
            timestamp = int(message.get("timestamp", 0))
            message_time = datetime.utcfromtimestamp(timestamp)
            if datetime.utcnow() - message_time > timedelta(minutes=5):
                logging.warning(f"⏳ Ignoring old message from {message_time}")
                return jsonify({"message": "Old message ignored"}), 200
            
            # ✅ Handle Interactive Messages (Button Replies)
            if message.get("type") == "interactive":
                interactive_response = message.get("interactive", {})
                
                if interactive_response.get("type") == "button_reply":
                    selection_id = interactive_response["button_reply"]["id"]
                    logging.info(f"📩 User selected payment option: {selection_id}")
                    # ✅ Handle payment selection
                    handle_payment_selection(from_number, selection_id)
                    return jsonify({"message": "Payment selection processed"}), 200
            
            # ✅ Check if the message contains an image
            if message.get('type') == 'image':
                image_id = message['image']['id']  # Get media ID
                logging.info(f"📷 Image received: {image_id}, determining action...")

                return handle_image_upload(from_number, image_id)
            
            # ✅ Prevent bot from looping on its own messages
            if message.get("from") == os.getenv("META_PHONE_NUMBER_ID"):
                logging.info(f"🛑 Ignoring bot-generated message to prevent infinite loops: {message.get('text', {}).get('body', '')}")
                return jsonify({"message": "Bot message ignored"}), 200


               
            incoming_message = message.get('text', {}).get('body', '').strip().lower()


            logging.info(f"📥 Message from {from_number}: {incoming_message}")
            logging.info(f"🛠 Full Webhook Payload: {json.dumps(data, indent=2)}")
            
            # ✅ Store the last 4 received messages for this user
            if from_number not in user_message_history:
                user_message_history[from_number] = []
            user_message_history[from_number].append(incoming_message)

            # ✅ Keep only the last 5 messages (remove older ones)
            if len(user_message_history[from_number]) > 5:
                user_message_history[from_number].pop(0)

             # ✅ Check for inactivity and update session summary if needed
            check_inactivity()

            #incoming_message_clean = incoming_message.strip().lower() if incoming_message else ""

              # ✅ **Check for Trigger Words Inside Messages**
            trigger_words = {
                "trigger_r", "trigger_rent_a_car", "trigger_tracking_service", "trigger_travel_booking",
                "trigger_towing_request", "trigger_honda_fit_rs", "trigger_honda_fit_gk3", "trigger_payment_button",
                "trigger_toyota_aqua", "trigger_honda_vezel", "trigger_mazda_cx5", "trigger_x-trail",
                "trigger_trail_blazer", "trigger_toyota_landcruiser_300_series", "trigger_toyota_prado", 
                "trigger_nissan_navara", "trigger_toyota_hilux_d4d", "trigger_ford_ranger", "trigger_honda_fits",
                "trigger_isuzu_x-rider", "trigger_nv350_9_seater", "trigger_nv350_13_seater", "trigger_toyota_fortuner",
                "trigger_1", "trigger_2", "trigger_3", "trigger_4", "trigger_5", "trigger_gd6_hilux",
                "trigger_suv", "trigger_fuel_saver", "trigger_double_cab", "trigger_mid_suv",
                "trigger_mini_buses", "trigger_luxury", "trigger_axio", "trigger_mercedes_benz_e_class",
                "trigger_mercedes_benz_c_class", "trigger_all_car_images", "trigger_toyota_gd6_hilux"
            }

           # ✅ Find All Matching Trigger Words Inside the Message
            detected_triggers = [word for word in trigger_words if re.search(rf'\b{word}\b', incoming_message)]

            if detected_triggers:
                for primary_trigger in detected_triggers:
                    logging.info(f"🛑 Detected trigger word in message: {primary_trigger}")

                    # ✅ Check if the message is from the bot to prevent infinite loops
                    if from_number == os.getenv("META_PHONE_NUMBER_ID"):  # BOT's own phone number
                        logging.info(f"✅ Skipping bot-generated trigger to prevent infinite loop: {primary_trigger}")
                        return jsonify({"message": "Bot trigger ignored"}), 200

                    # ✅ Process user-generated trigger normally
                    response_data = {"user_selection": primary_trigger.replace("trigger_", "")}

                    if primary_trigger in ["trigger_suv", "trigger_fuel_saver", "trigger_double_cab",
                                        "trigger_mid_suv", "trigger_mini_buses", "trigger_luxury"]:
                        send_specific_car(response_data, message)

                    elif primary_trigger in ["trigger_1", "trigger_2", "trigger_3", "trigger_4", "trigger_5"]:
                        process_user_selection(response_data, message)

                    elif primary_trigger in [
                        "trigger_honda_fit_rs", "trigger_honda_fit_gk3", "trigger_toyota_aqua", "trigger_honda_vezel",
                        "trigger_mazda_cx5", "trigger_x-trail", "trigger_trail_blazer", "trigger_toyota_landcruiser_300_series",
                        "trigger_toyota_prado", "trigger_nissan_navara", "trigger_toyota_hilux_d4d", "trigger_ford_ranger",
                        "trigger_isuzu_x-rider", "trigger_nv350_9_seater", "trigger_nv350_13_seater", "trigger_axio",
                        "trigger_gd6_hilux", "trigger_all_car_images", "trigger_toyota_fortuner", "trigger_toyota_gd6_hilux",
                        "trigger_mercedes_benz_e_class", "trigger_honda_fits"
                    ]:
                        send_car_model(response_data, message)

                    elif primary_trigger in ["trigger_r", "trigger_restart"]:
                        session.clear()
                        send_whatsapp_message(from_number, "🔄 Your session has been reset. Let's start afresh now!", is_bot_message=True)

                    elif primary_trigger in ["trigger_payment_button"]:
                        trigger_payment_button(from_number)

                return jsonify({"message": "Trigger processed"}), 200  # ✅ Stop further processing after triggers

                        
            # ✅ Proceed to process bot response after handling all user triggers
            # ✅ Get the bot's response
            bot_reply = query_openai_model(incoming_message, from_number)
            

            # ✅ Find All Matching Trigger Words in Bot Reply
            detected_triggers = [word for word in trigger_words if re.search(rf'\b{word}\b', bot_reply.lower())]    
            send_whatsapp_message(from_number, bot_reply, is_bot_message=True)

        except Exception as e:
            logging.error(f"❌ Error Processing Webhook: {e}")
            logging.error(traceback.format_exc())

def check_inactivity():
    """
    Check if users have been inactive for more than 3 hours and update session memory if needed.
    """
    conn = get_db_connection()
    if not conn:
        return

    try:
        cursor = conn.cursor()

        # 1️⃣ Retrieve all distinct user phone numbers
        cursor.execute('SELECT DISTINCT contact_number FROM apex_customers')
        users = cursor.fetchall()

        for user in users:
            from_number = user[0]

            # 2️⃣ Retrieve the timestamp of the last message received
            cursor.execute('''
                SELECT timestamp FROM conversations
                WHERE from_number = %s
                ORDER BY timestamp DESC
                LIMIT 1
            ''', (from_number,))
            last_message = cursor.fetchone()

            if not last_message:
                logging.info(f"🔍 No previous activity found for {from_number}. Skipping inactivity check.")
                continue

            last_message_time = last_message[0]

            # 3️⃣ Retrieve the timestamp of the last session summary
            cursor.execute('''
                SELECT created_at FROM user_memory 
                WHERE contact_number = %s AND memory_key = 'session_summary'
                ORDER BY created_at DESC
                LIMIT 1
            ''', (from_number,))
            last_summary = cursor.fetchone()
            last_summary_time = last_summary[0] if last_summary else None

            now = datetime.now()

            # 4️⃣ Calculate the time since the last summary and last message
            time_since_last_summary = (now - last_summary_time).total_seconds() if last_summary_time else None
            time_since_last_message = (now - last_message_time).total_seconds()

            # 5️⃣ Ensure at least *3 hours (10800 seconds) have passed* & new messages exist
            if (last_summary_time is None) or (
                time_since_last_summary > 10800 and last_message_time > last_summary_time
            ):
                logging.info(f"🔄 Session for {from_number} summarized due to inactivity and new messages detected.")

        cursor.close()
        conn.close()

    except psycopg2.Error as e:
        logging.error(f"Database error while checking inactivity: {e}")

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

 

schedule.every(3).hours.do(check_inactivity)

def run_scheduler():
    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == '__main__':
    scheduler_thread = threading.Thread(target=run_scheduler)
    scheduler_thread.daemon = True
    scheduler_thread.start()
    app.run(debug=False, use_reloader=False,host="0.0.0.0", port=5000)
