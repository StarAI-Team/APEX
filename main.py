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
from base64 import b64decode, b64encode
from flask import Flask, request, jsonify, Response
from base64 import b64decode, b64encode
from cryptography.hazmat.primitives.asymmetric.padding import OAEP, MGF1
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from cryptography.hazmat.primitives import hashes
from drive_upload import upload_to_google_drive



# Load environment variables from .env file
load_dotenv()

# Load the private key string from environment variables
with open("whatsapp_flow_private_key.pem", "r") as key_file:
    PRIVATE_KEY = key_file.read()


rental_sessions = {}
towing_sessions = {}
travel_sessions = {}
user_data = {}
processed_messages = set()  # ✅ Stores processed message IDs to prevent duplicates



# Set up environment variables for Meta API
os.environ['META_PHONE_NUMBER_ID'] 
os.environ['META_ACCESS_TOKEN']
os.environ['OPENAI_API_KEY'] 


FLOW_IDS = {
    "rental_flow": os.getenv("WHATSAPP_FLOW_RENTAL"),
    "tracking_flow": os.getenv("WHATSAPP_FLOW_TRACKING"),
    "towing_flow": os.getenv("WHATSAPP_FLOW_TOW"),
    "travel_flow": os.getenv("WHATSAPP_FLOW_TRAVEL"),
    "Payment":os.getenv("WHATSAPP_FLOW_PAYMENT"),
    "Rating":os.getenv("WHATSAPP_FLOW_RATING"),
    "name_capture_flow":os.getenv("WHATSAPP_FLOW_NAME_CAPTURE")
}



 #Initialize OpenAI client
client = OpenAI(
    api_key=os.environ.get("OPENAI_API_KEY")
)

OPENAI_ASSISTANT_ID = os.getenv("OPENAI_ASSISTANT_ID")

# Configure Logging
logging.basicConfig(
    filename="agent.log",  
    level=logging.DEBUG,  
    format="%(asctime)s - %(levelname)s - %(message)s"
)


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



# def get_user_preferences(contact_number):
#     """
#     Retrieve user preferences and memory from the database.
#     """
#     conn = get_db_connection()
#     if not conn:
#         return {}
#     try:
#             cursor = conn.cursor()
#             cursor.execute('SELECT memory_key, value FROM user_memory WHERE contact_number = %s', (contact_number,))
#             preferences = {row[0]: row[1] for row in cursor.fetchall()}
#             return preferences
#     except psycopg2.Error as e:
#         logging.error(f"Database error while retrieving preferences: {e}")
#         preferences = {}
#     finally:
#         cursor.close()
#         conn.close()

#     return preferences




# def summarize_session(contact_number, current_conversation=None):
#     """
#     Retrieve past session summaries, user preferences, and recent messages to provide full context.
#     """
#     try:
#         logging.info(f"🔍 Summarizing session for {contact_number}...")
#         conn = get_db_connection()
#         if not conn:
#             logging.error("❌ Database connection failed in summarize_session()")
#             return "No conversation history available."

#         cursor = conn.cursor()

#         # 1️⃣ Retrieve stored session summary from user_memory
#         cursor.execute('''
#             SELECT value FROM user_memory WHERE contact_number = %s AND memory_key = 'session_summary'
#         ''', (contact_number,))
#         stored_summary = cursor.fetchone()
#         stored_summary = stored_summary[0] if stored_summary else "No previous context available."
#         logging.info(f"📝 Stored Summary Retrieved: {stored_summary}")

#         # 2️⃣ Retrieve last 5–10 messages for immediate context (✅ Fixed table name)
#         cursor.execute('''
#             SELECT message, bot_reply FROM conversations 
#             WHERE from_number = %s
#             ORDER BY timestamp DESC
#             LIMIT 10
#         ''', (contact_number,))
#         recent_messages = cursor.fetchall()

#         if not recent_messages:
#             logging.warning(f"⚠️ No recent messages found for {contact_number}")

#         # Format messages for OpenAI
#         recent_history = "\n".join([f"User: {msg} | Bot: {resp}" for msg, resp in recent_messages])
#         logging.info(f"📜 Recent Messages Retrieved: {recent_history}")

#         # 3️⃣ Retrieve user preferences
#         preferences = get_user_preferences(contact_number)
#         preferences_summary = ", ".join([f"{key}: {value}" for key, value in preferences.items()]) if preferences else "No preferences stored."
#         logging.info(f"🔧 User Preferences Retrieved: {preferences_summary}")

#         # 4️⃣ Include ongoing messages if available
#         if current_conversation and isinstance(current_conversation, list):
#             ongoing_conversation = "\n".join(
#                 [f"User: {msg['content']}" if isinstance(msg, dict) and "content" in msg else "Invalid data" for msg in current_conversation]
#             )
#         else:
#             ongoing_conversation = ""

#         logging.info(f"💬 Ongoing Conversation: {ongoing_conversation}")

#         # 5️⃣ Merge stored summary, user preferences, recent messages, and ongoing messages
#         full_context = f"""
#         **User Preferences:** 
#         {preferences_summary}

#         **Stored Summary:** 
#         {stored_summary}

#         **Recent Messages:** 
#         {recent_history}

#         **Ongoing Messages:** 
#         {ongoing_conversation}
#         """
#         logging.info(f"🔗 Full Context for OpenAI: {full_context}")

#         # 6️⃣ Query OpenAI for a refined session summary (✅ Fixed response handling)
#         response = client.chat.completions.create(
#             model="gpt-4",
#             messages=[
#                 {"role": "system", "content": "Summarize this conversation while incorporating user preferences for better context."},
#                 {"role": "user", "content": full_context}
#             ]
#         )

#         if response and hasattr(response, 'choices') and len(response.choices) > 0:
#             first_choice = response.choices[0]
#             logging.info(f"🔍 OpenAI First Choice Response: {first_choice}")

#             # ✅ Handle OpenAI response as both an object and a dictionary
#             if isinstance(first_choice, dict) and "message" in first_choice and "content" in first_choice["message"]:
#                 summary = first_choice["message"]["content"].strip()
#             elif hasattr(first_choice, "message") and hasattr(first_choice.message, "content"):
#                 summary = first_choice.message.content.strip()
#             else:
#                 logging.error(f"❌ Unexpected OpenAI response format: {first_choice}")
#                 summary = "No relevant details summarized."

#             logging.info(f"✅ Generated Session Summary: {summary}")

#             return {"role": "assistant", "content": summary, "contact_number": contact_number}

#         logging.warning("⚠️ OpenAI did not return a valid summary.")
#         return {"role": "assistant", "content": "No relevant details summarized.", "contact_number": contact_number}

#     except Exception as e:
#         logging.error(f"❌ Error summarizing session: {e}")
#         return {"role": "assistant", "content": "Error summarizing session.", "contact_number": contact_number}

#     finally:
#         cursor.close()
#         conn.close()



def save_session_to_db(contact_number, session_summary):
    """
    Save or update session summary in PostgreSQL.
    """
    conn = get_db_connection()
    if not conn:
        logging.error("❌ Database connection failed. Cannot save session summary.")
        return

    try:
        cursor = conn.cursor()

        # ✅ Extract only the summary text if session_summary is a dictionary
        if isinstance(session_summary, dict) and "content" in session_summary:
            session_summary = session_summary["content"]

        # ✅ Ensure it’s a string before saving
        if isinstance(session_summary, list) or isinstance(session_summary, dict):
            session_summary = json.dumps(session_summary)

        cursor.execute('''
            INSERT INTO user_memory (contact_number, memory_key, value, created_at)
            VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (contact_number, memory_key) 
            DO UPDATE SET value = EXCLUDED.value, created_at = CURRENT_TIMESTAMP
        ''', (contact_number, 'session_summary', session_summary))

        conn.commit()
        logging.info(f"✅ Session summary saved for {contact_number}")

    except psycopg2.Error as e:
        logging.error(f"❌ Database error while saving session summary: {e}")

    finally:
        cursor.close()
        conn.close()





# @app.route('/end-session', methods=['POST'])
# def end_session():
#     try:
#         from_number = request.values.get('From', '').strip()

#         # Summarize session
#         session_summary = summarize_session(session.get('conversation', []))

#         # Save summary to database
#         save_session_to_db(from_number, session_summary)

#         # Clear session
#         session.clear()

#         return "Session ended and data saved."
#     except Exception as e:
#         logging.error(f"Error ending session: {e}")
#         return "Error ending session."
    

def get_vehicles():
    """Retrieve vehicle details from the database, including image URLs."""
    conn = get_db_connection()
    if not conn:
        return {}

    try:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT 
                class, 
                details, 
                rate_per_day, 
                can_be_discounted_to, 
                refundable_deposit, 
                allocated_mileage_per_day, 
                excess_mileage_charge_per_km,
                images
            FROM vehicles 
            WHERE available = TRUE 
            ORDER BY class
        ''')
        vehicle_records = cursor.fetchall()

        # Organize vehicles by class
        vehicles = {}
        for (class_name, details, rate_per_day, can_be_discounted_to, refundable_deposit, 
             allocated_mileage_per_day, excess_mileage_charge_per_km, images) in vehicle_records:
            
            if class_name not in vehicles:
                vehicles[class_name] = []
            
            vehicle_info = {
                "details": details,
                "rate_per_day": f"${rate_per_day:.2f}",
                "discounted_to": f"${can_be_discounted_to:.2f}",
                "refundable_deposit": f"${refundable_deposit:.2f}",
                "allocated_mileage_per_day": f"{allocated_mileage_per_day} km",
                "excess_mileage_charge_per_km": f"${excess_mileage_charge_per_km}/km",
                "images": images  # ✅ Include the image URL
            }

            vehicles[class_name].append(vehicle_info)

    except psycopg2.Error as e:
        logging.warning(f"Database error while retrieving vehicles: {e}")
        vehicles = {}

    finally:
        cursor.close()
        conn.close()  # Ensure connection is closed

    return vehicles




 

def save_towing_request(pickup_location, pickup_address, contact_person, phone_number, type_of_car, type_of_assist_link, drop_location, receiver, receiver_contact_details):
    """Save towing request in the database with logging."""
    
    # ✅ Log the received data before inserting into the database
    logging.info(f"🔍 Data Received for save_towing_request:\n"
                 f"📍 Pickup Location: {pickup_location}\n"
                 f"🏠 Pickup Address: {pickup_address}\n"
                 f"👤 Contact Person: {contact_person}\n"
                 f"📞 Phone Number: {phone_number}\n"
                 f"🚗 Vehicle Type: {type_of_car}\n"
                 f"🛠️ Type of Assistance: {type_of_assist_link}\n"
                 f"📍 Drop Location: {drop_location}\n"
                 f"👤 Receiver: {receiver}\n"
                 f"📞 Receiver Contact: {receiver_contact_details}")

    conn = get_db_connection()
    if not conn:
        logging.error("❌ Database connection failed.")
        return "❌ Database connection failed."

    try:
        cursor = conn.cursor()

        # ✅ Log the SQL command before executing it
        logging.info("📤 Executing SQL Insert for towing request...")

        # Insert towing data into the 'towing' table
        cursor.execute('''
            INSERT INTO towing (pickuplocation, pickupaddress, contactperson, phonenumber, typeofcar, typeofassist, droplocation, receiver, receivercontactdetails)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (pickup_location, pickup_address, contact_person, phone_number, type_of_car, type_of_assist_link, drop_location, receiver, receiver_contact_details))

        conn.commit()
        logging.info(f"✅ Towing request successfully saved for {contact_person}")

        update_user_status(contact_person, "towing")

        notification_message = (
            f"🚨 *New Towing Request* 🚨\n\n"
            f"📍 *Pickup Location:* {pickup_location}\n"
            f"🏠 *Pickup Address:* {pickup_address}\n"
            f"👤 *Contact Person:* {contact_person}\n"
            f"📞 *Phone Number:* {phone_number}\n"
            f"🚗 *Vehicle Type:* {type_of_car}\n"
            f"🛠️ *Type of Assistance:* {type_of_assist_link} (View Image)\n"
            f"📍 *Drop Location:* {drop_location}\n"
            f"👤 *Receiver:* {receiver}\n"
            f"📞 *Receiver Contact:* {receiver_contact_details}\n"
        )
        
        send_whatsapp_message(ADMIN_NUMBER, notification_message)
        logging.info("📤 Admin notified about the towing request.")

        return f"✅ Towing request successfully recorded for {contact_person}. Assistance details saved."
    
    except psycopg2.Error as e:
        logging.error(f"❌ Database error while saving towing request: {e.pgcode} - {e.pgerror}")
        return "❌ Error saving towing request. Please try again."

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()  # Ensure connection is closed
    
def save_rentals(customer_name, licence_drive_link, selfie_drive_link, proof_drive_link, vehicle_hired, deposit_paid=False, completed=False):
    """Save rental transaction, log it in OpenAI's memory, and notify admin."""

    conn = get_db_connection()
    if not conn:
        return "❌ Database connection failed."

    try:
        cursor = conn.cursor()

        # ✅ Insert rental data into the 'rentals' table
        cursor.execute('''
            INSERT INTO rentals (customername, licence, selfie, proofofresidence, vehiclehired, depositpaid, completed)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        ''', (customer_name, licence_drive_link, selfie_drive_link, proof_drive_link, vehicle_hired, deposit_paid, completed))
        
        conn.commit()

        update_user_status(customer_name, "rentals")

        # ✅ Retrieve or create an OpenAI thread for the user
        thread_id = get_user_thread(customer_name)

        if not thread_id:
            thread = client.beta.threads.create()
            thread_id = thread.id
            save_user_thread(customer_name, thread_id)
            logging.info(f"🆕 New thread created for rental request: {thread_id}")

        # ✅ Log the rental request in OpenAI's memory
        rental_details = (
            f"🚗 *New Rental Request Confirmed!*\n"
            f"👤 Customer: {customer_name}\n"
            f"📜 Licence: {licence_drive_link}\n"
            f"📸 Selfie: {selfie_drive_link}\n"
            f"🏠 Proof of Residence: {proof_drive_link}\n"
            f"🚘 Vehicle Hired: {vehicle_hired}\n"
            f"💰 Deposit Paid: {'Yes' if deposit_paid else 'No'}\n"
            f"✅ Completed: {'Yes' if completed else 'No'}"
        )

        client.beta.threads.messages.create(
            thread_id=thread_id,
            role="assistant",
            content=rental_details
        )
        logging.info(f"✅ Rental request logged in OpenAI thread for {customer_name}")

        # ✅ Notify admin via WhatsApp
        send_whatsapp_message(ADMIN_NUMBER, rental_details)

        return "✅ Your rental request has been received. Please proceed with the $200 deposit."

    except psycopg2.Error as e:
        logging.error(f"❌ Database error while saving rental: {e.pgcode} - {e.pgerror}")

        return "❌ There was an issue saving your rental. Please try again later."
    
    finally:
        cursor.close()
        conn.close()  # Ensure connection is closed


    
def save_travel_booking(customer_name, proof_of_residence_url, destination, driver, num_of_days, tour, vehicle_type):
    """Save travel booking details, log them in OpenAI's memory, and notify admin."""

    conn = get_db_connection()
    if not conn:
        return "❌ Database connection failed."

    try:
        cursor = conn.cursor()

        # ✅ Insert travel booking data into the 'travels' table
        cursor.execute('''
            INSERT INTO travels (customername, proofofresidence, depositpaid, completed, destination, driver, num_of_days, tour, vehicle_type)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (customer_name, proof_of_residence_url, False, False, destination, driver, num_of_days, tour, vehicle_type))

        conn.commit()
        update_user_status(customer_name, "travel")

        # ✅ Retrieve or create an OpenAI thread for the user
        thread_id = get_user_thread(customer_name)

        if not thread_id:
            thread = client.beta.threads.create()
            thread_id = thread.id
            save_user_thread(customer_name, thread_id)
            logging.info(f"🆕 New thread created for travel booking: {thread_id}")

        # ✅ Log the travel booking in OpenAI's memory
        travel_details = (
            f"🚗 *New Travel Booking Confirmed!*\n"
            f"👤 Customer: {customer_name}\n"
            f"🏠 Proof of Residence: {proof_of_residence_url}\n"
            f"📍 Destination: {destination}\n"
            f"🛞 Vehicle Type: {vehicle_type}\n"
            f"👨‍✈️ Driver Needed: {'Yes' if driver else 'No'}\n"
            f"📅 Number of Days: {num_of_days}\n"
            f"🌍 Tour Included: {'Yes' if tour else 'No'}\n"
            f"💰 Deposit Paid: ❌ No (Pending Payment)\n"
            f"✅ Completed: ❌ No"
        )

        client.beta.threads.messages.create(
            thread_id=thread_id,
            role="assistant",
            content=travel_details
        )
        logging.info(f"✅ Travel booking logged in OpenAI thread for {customer_name}")

        # ✅ Notify admin via WhatsApp
        send_whatsapp_message(ADMIN_NUMBER, travel_details)

        return "✅ Your travel booking has been received. Please proceed with the $200 deposit."

    except psycopg2.Error as e:
        logging.error(f"❌ Database error while saving travel booking: {e}")
        return "❌ There was an issue saving your travel booking. Please try again later."

    finally:
        cursor.close()
        conn.close()


def save_tracking_request(user_name, address, phone_number, vehicle_make, color, registration_number):
    """Save tracking request in the 'tracking' table, log it in OpenAI, and notify admin."""

    conn = get_db_connection()
    if not conn:
        return "❌ Database connection failed."

    try:
        cursor = conn.cursor()

        # ✅ Insert tracking request into the 'tracking' table
        cursor.execute('''
            INSERT INTO tracking (username, address, phonenumber, vehiclemake, color, registrationnumber)
            VALUES (%s, %s, %s, %s, %s, %s)
        ''', (user_name, address, phone_number, vehicle_make, color, registration_number))

        conn.commit()

        # ✅ Update user status
        update_user_status(phone_number, "tracking")

        # ✅ Retrieve or create an OpenAI thread for the user
        thread_id = get_user_thread(phone_number)  # Using phone_number instead of from_number

        if not thread_id:
            thread = client.beta.threads.create()
            thread_id = thread.id
            save_user_thread(phone_number, thread_id)
            logging.info(f"🆕 New thread created for tracking request: {thread_id}")

        # ✅ Log the tracking request in OpenAI's memory
        tracking_details = (
            f"📡 *New Tracking Profile Created!*\n"
            f"👤 Name: {user_name}\n"
            f"🏠 Address: {address}\n"
            f"📞 Phone Number: {phone_number}\n"
            f"🚗 Vehicle: {vehicle_make}\n"
            f"🎨 Color: {color}\n"
            f"🔢 Registration Number: {registration_number}\n"
            f"✅ User successfully registered in the tracking system."
        )

        client.beta.threads.messages.create(
            thread_id=thread_id,
            role="assistant",
            content=tracking_details
        )
        logging.info(f"✅ Tracking request logged in OpenAI thread for {phone_number}")

        # ✅ Notify admin via WhatsApp
        send_whatsapp_message(ADMIN_NUMBER, tracking_details)

        return f"✅ Tracking request successfully recorded for {user_name}. Tracking profile created."

    except psycopg2.Error as e:
        logging.error(f"❌ Database error while saving tracking request: {e}")
        return "❌ Error saving tracking request."

    finally:
        cursor.close()
        conn.close()



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




def update_user_status(contact_number, new_status):
    """
    Add a new status to a customer without removing existing ones.
    If a status already exists, append the new one.
    """
    conn = get_db_connection()  # Use PostgreSQL connection
    if not conn:
        return

    try:
        cursor = conn.cursor()

        # Fetch current status
        cursor.execute('SELECT status FROM customers WHERE contact_number = %s', (contact_number,))
        row = cursor.fetchone()

        if row:
            existing_status = row[0]
            status_list = existing_status.split(", ") if existing_status else []

            if new_status not in status_list:
                status_list.append(new_status)  # Append new status if not already present
                updated_status = ", ".join(status_list)

                # Update status in the database
                cursor.execute('''
                    UPDATE customers SET status = %s WHERE contact_number = %s
                ''', (updated_status, contact_number))
                conn.commit()
                logging.info(f"✅ Updated status for {contact_number} to {updated_status}")
        else:
            # If user has no status, insert a new record
            cursor.execute('''
                INSERT INTO customers (contact_number, status) VALUES (%s, %s)
            ''', (contact_number, new_status))
            conn.commit()
            logging.info(f"✅ New user status set: {contact_number} → {new_status}")

    except psycopg2.Error as e:
        logging.error(f"❌ Database error while updating status: {e}")

    finally:
        cursor.close()
        conn.close()  # Ensure connection is closed






@app.route('/clear-session', methods=['GET'])
def clear_session():
    """
    Clear the entire session.
    """
    try:
        session.clear()  # Clear all session variables
        return "Session has been cleared.", 200
    except Exception as e:
        logging.error(f"Error clearing session: {e}")
        return "Error clearing session.", 500

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



# def query_openai_model(user_message, session_summary, formatted_history):
#     try:
#         logging.debug(f"Debug: Querying OpenAI API with -> {user_message}")

#         # Fetch the daily vehicle
#         try:
#             available_vehicles = get_vehicles()  # Ensure this function exists
#         except Exception as e:
#             logging.error(f"Error fetching vehicles: {e}")
#             available_vehicles = None

#         if not available_vehicles:
#             vehicle_text = "Vehicle is currently unavailable. Please try again later."
#         else:
#             vehicle_text = "\n".join(
#                 f"{category}:\n" + "\n".join(items) for category, items in available_vehicles.items()
#             )

#         # System role definition
#         system_role = {
#             "role": "system",
#             "content": (
#                 "You are APEX, a highly intelligent and friendly customer assistant for APEX TRAVELS, "
#                 "a trusted company offering vehicle rentals, travel bookings, towing services, and tracking assistance. "
#                 "Your primary role is to assist customers efficiently, politely, and professionally.\n\n"

#                 "📌 **Context from Previous Messages:**\n"
#                 f"{session_summary}\n\n"
#                 "📜 **Full Conversation History:**\n"
#                 f"{formatted_history}\n\n"
#                 "🚘 **Available Vehicles:**\n"
#                 f"{vehicle_text}\n\n"
#                 "🔵 **Current User Message:**\n"
#                 f"User: {user_message}\n\n"
#                 "Your response should continue the conversation smoothly.\n\n"

#                 "1. **Greeting Customers**\n"
#                 "   - Greet customers warmly with their name during the first interaction.\n"
#                 "   - Avoid repeating introductory greetings unless explicitly asked.\n\n"

#                 "2. **Towing Assistance 🚗**\n"
#                 "   - If a user needs towing, guide them to submit their request.\n"
#                 "   - Send them this:\n"
#                 "     '🚨 Need towing assistance? Simply type *towing request* and I will walk you through the process!'\n\n"

#                 "3. **Vehicle Rentals 🚘**\n"
#                 "   - If a user wants to rent a car, provide details and booking instructions.\n"
#                 "   - Send them this:\n"
#                 "     '🚗 Looking to rent a car? Simply type *rent a car* and I’ll help you book one!'\n\n"

#                 "4. **Travel Bookings ✈️**\n"
#                 "   - If a user wants to book travel, guide them to complete the process.\n"
#                 "   - Send them this:\n"
#                 "     '🌍 Planning a trip? Type *book a trip* and I’ll assist you with the details!'\n\n"

#                 "5. **Tracking Requests 📦**\n"
#                 "   - If a user wants to track their request, help them access updates.\n"
#                 "   - Send them this:\n"
#                 "     '📍 Need to track your request? Type *track my request* and I’ll get your latest updates!'\n\n"

#                 "6. **Providing Support for Special Requests**\n"
#                 "   - Handle customer queries about vehicle preferences, trip planning, or emergencies.\n"
#                 "   - Example: 'Absolutely! Let me know if you have special requests, and I will assist you accordingly.'\n\n"

#                 "7. **Canceling Requests & Reservations**\n"
#                 "   - Users can cancel a request before it's processed.\n"
#                 "   - Provide the correct format for cancellation:\n"
#                 "     - 'Cancel towing request for [Vehicle Type]'\n"
#                 "     - 'Cancel rental for [Car Model]'\n"
#                 "     - 'Cancel travel booking for [Destination]'\n\n"

#                 "8. **Clarifying Unclear Messages**\n"
#                 "   - If a message is unclear, politely ask for clarification.\n\n"

#                 "9. **Using OpenAI API for Uncommon Questions**\n"
#                 "   - When faced with unique questions or unsupported requests, leverage the OpenAI API for intelligent responses.\n\n"

#                 "10. **Tone and Personality**\n"
#                 "   - Maintain a polite, friendly, and professional tone.\n"
#                 "   - Express gratitude frequently to build a positive rapport.\n"
#                 "   - Add the customer's name to the farewell message.\n"
#                 "   - Example: 'Thank you for choosing APEX Travels, [Customer Name]! We are delighted to assist you!'\n"
#                 "   - Stay patient and adaptable to customer needs."
#             )
#         }

#         # Query OpenAI API
#         try:
#             response = client.chat.completions.create(
#                 model="gpt-4o-mini",
#                 messages=[
#                     system_role,
#                     {"role": "user", "content": user_message}
#                 ]
#             )

#             logging.info(f"Debug: OpenAI Response -> {response}")

#             # Extract the first message from the assistant's reply
#             return response.choices[0].message.content if response.choices else "Unexpected response format from the OpenAI API."

#         except Exception as e:
#             logging.error(f"Error querying OpenAI API: {e}")
#             return "There was an error processing your request. Please try again later."

#     except Exception as e:
#         logging.error(f"Unexpected error: {e}")
#         return "A system error occurred. Please try again later."


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



import psycopg2
import logging
from openai import OpenAI

# Initialize OpenAI client
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

def get_db_connection():
    """Establishes a connection to the PostgreSQL database."""
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
        logging.error(f"❌ Database connection error: {e}")
        return None

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

def query_openai_model(user_message, from_number):
    """
    Sends user input to OpenAI, ensuring all past conversations and transactions remain within a persistent thread.
    """

    try:
        # Retrieve or create a thread ID for the user
        thread_id = get_user_thread(from_number)

        if not thread_id:
            thread = client.beta.threads.create()
            thread_id = thread.id
            save_user_thread(from_number, thread_id)
            logging.info(f"🆕 New thread created for {from_number}: {thread_id}")
        else:
            logging.info(f"🔄 Reusing existing thread for {from_number}: {thread_id}")

        # Retrieve past transactions from the database
        past_transactions = get_user_transactions(from_number)  # Function to fetch transactions


        # Send system prompt + user message to OpenAI
        client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=user_message
        )

        # Run the assistant on this thread
        run = client.beta.threads.runs.create(
            thread_id=thread_id,
            assistant_id=OPENAI_ASSISTANT_ID
        )

        # Wait for completion
        while run.status != "completed":
            run = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run.id)

        # Retrieve the assistant's latest response
        messages = client.beta.threads.messages.list(thread_id=thread_id)
        bot_reply = messages.data[0].content[0].text.value  # Extract text

        # Log conversation (Optional for analytics)
        log_conversation(from_number, user_message, bot_reply, "processed")

        logging.info(f"🤖 Assistant Response to {from_number}: {bot_reply}")
        return bot_reply

    except Exception as e:
        logging.error(f"❌ Error in query_openai_model for {from_number}: {str(e)}")
        return "Sorry, I encountered an error. Please try again."


def get_user_transactions(from_number):
    """
    Retrieves all transactions (travel bookings, rentals, towing, and tracking) for the user,
    including full details, and formats them for OpenAI.
    """
    conn = get_db_connection()
    if not conn:
        return "No transactions recorded."

    try:
        cursor = conn.cursor()

        # Fetch all travel bookings
        cursor.execute("""
            SELECT customername, destination, num_of_days, vehicle_type, driver, created_at
            FROM travels  
            WHERE customername = %s
        """, (from_number,))
        travels = cursor.fetchall()

        # Fetch all rentals
        cursor.execute("""
            SELECT customername, vehiclehired, licence, selfie, proofofresidence, depositpaid, completed, created_at
            FROM rentals  
            WHERE customername = %s
        """, (from_number,))
        rentals = cursor.fetchall()

        # Fetch all towing requests
        cursor.execute("""
            SELECT contactperson, pickuplocation, pickupaddress, phonenumber, typeofcar, typeofassist, droplocation, receiver, receivercontactdetails, created_at
            FROM towing  
            WHERE contactperson = %s
        """, (from_number,))
        towing_requests = cursor.fetchall()

        # Fetch all tracking requests
        cursor.execute("""
            SELECT username, address, phonenumber, vehiclemake, color, registrationnumber, created_at
            FROM tracking  
            WHERE phonenumber = %s
        """, (from_number,))
        tracking_requests = cursor.fetchall()

        cursor.close()
        conn.close()

        # Format transactions
        transaction_list = []

        # Format travel bookings
        if travels:
            transaction_list.append("📅 **Past Travel Bookings:**")
            for customername, destination, num_of_days, vehicle_type, driver, created_at in travels:
                driver_status = "Yes" if driver else "No"
                transaction_list.append(f"- 🏝️ {customername} booked a trip to {destination} for {num_of_days} days "
                                        f"with a {vehicle_type}. Driver Included: {driver_status}. "
                                        f"(Booked on {created_at.strftime('%Y-%m-%d %H:%M')})")

        # Format rentals
        if rentals:
            transaction_list.append("\n🚗 **Past Car Rentals:**")
            for customername, vehiclehired, licence, selfie, proofofresidence, depositpaid, completed, created_at in rentals:
                deposit_status = "Paid" if depositpaid else "Not Paid"
                completion_status = "Completed" if completed else "Pending"
                transaction_list.append(f"- 🚘 {customername} rented a {vehiclehired} (License: {licence}). "
                                        f"📸 Selfie: {selfie}. 🏠 Proof: {proofofresidence}. "
                                        f"Deposit: {deposit_status}. Status: {completion_status}. "
                                        f"(Rented on {created_at.strftime('%Y-%m-%d %H:%M')})")

        # Format towing requests
        if towing_requests:
            transaction_list.append("\n🛠️ **Past Towing Requests:**")
            for contactperson, pickuplocation, pickupaddress, phonenumber, typeofcar, typeofassist, droplocation, receiver, receivercontactdetails, created_at in towing_requests:
                transaction_list.append(f"- 🚨 {contactperson} requested towing for a {typeofcar} from {pickuplocation} "
                                        f"({pickupaddress}) to {droplocation}. Assistance Type: {typeofassist}. "
                                        f"Receiver: {receiver} ({receivercontactdetails}). "
                                        f"(Requested on {created_at.strftime('%Y-%m-%d %H:%M')})")

        # Format tracking requests
        if tracking_requests:
            transaction_list.append("\n📡 **Past Tracking Requests:**")
            for username, address, phonenumber, vehiclemake, color, registrationnumber, created_at in tracking_requests:
                transaction_list.append(f"- 📍 {username} requested tracking for a {vehiclemake} ({color}) with registration number {registrationnumber}. "
                                        f"Address: {address}. (Tracked on {created_at.strftime('%Y-%m-%d %H:%M')})")

        return "\n".join(transaction_list) if transaction_list else "No recorded transactions."

    except psycopg2.Error as e:
        logging.error(f"❌ Database error retrieving transactions: {e}")
        return "Error retrieving transactions."

# Send WhatsApp message
def send_whatsapp_message(to, message=None, images=None, flow_id=None):
    """
    Sends a WhatsApp message.
    - If `flow_id` is provided, sends a WhatsApp Flow.
    - If `images` is provided, sends an image message.
    - Otherwise, sends a normal text message.
    """
    PHONE_NUMBER_ID = os.getenv("META_PHONE_NUMBER_ID")  # WhatsApp Business API Phone Number ID
    ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN")  # Your API Access Token

    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"

    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }

    # ✅ Send WhatsApp Flow if `flow_id` is provided
    if flow_id:
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "flow",
                "flow": {
                    "flow_message_version": "3",
                    "flow_id": flow_id  
                }
            }
        }
    # ✅ Send an image if `images` is provided
    elif images:
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "image",
            "image": {
                "link": images  # ✅ Image URL from your database or Google Drive
            }
        }
    # ✅ Send a normal text message if no `image_url` or `flow_id`
    else:
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": message},
        }

    response = requests.post(url, headers=headers, json=payload)

    if response.status_code == 200:
        print(f"✅ Message sent successfully to {to}")
    else:
        print(f"❌ Error sending message: {response.text}")



def trigger_whatsapp_flow(to_number, message, flow_cta, flow_name):
    """
    Sends a request to trigger a WhatsApp Flow dynamically using flow_name.
    """
    PHONE_NUMBER_ID = os.getenv("META_PHONE_NUMBER_ID")
    ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN")

    # ✅ Retrieve Flow ID dynamically
    flow_id = FLOW_IDS.get(flow_name)
    
    if not PHONE_NUMBER_ID or not ACCESS_TOKEN or not flow_id:
        logging.error(f"❌ Missing required variables! Flow Name: {flow_name}, Flow ID: {flow_id}")
        return "Error: Missing environment variables or flow ID."

    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_number,
        "type": "interactive",
        "interactive": {
            "type": "flow",
            "body": {"text": message},
            "action": {
                "name": "flow",
                "parameters": {
                    "flow_id": flow_id,  
                    "flow_cta": flow_cta,
                    "flow_token": 'rufaro_is_a_genius',
                    "flow_message_version": 3,
                    "mode": 'published',
                }
            }
        }
    }

    logging.debug(f"Sending WhatsApp Flow Request: {json.dumps(payload, indent=2)}")

    response = requests.post(url, headers=headers, json=payload)
    logging.debug(f"🔍 WhatsApp API Response ({response.status_code}): {response.text}")

    return "Flow triggered successfully." if response.status_code == 200 else f"Error triggering flow: {response.text}"


def process_towing_flow(response_data, message):
    """
    Step 1️⃣: Extract text from WhatsApp Flow.
    Step 2️⃣: Prompt user to upload an image for 'Type of Assistance'.
    Step 3️⃣: Receive the image, upload it to Drive, then save towing request.
    """
    from_number = message.get("from", "")
    session_id = from_number  # Unique session identifier

    # ✅ Extract Data Dynamically Using Partial Key Matching
    pickup_location = next((value for key, value in response_data.items() if "Pickup_Location" in key), None)
    pickup_address = next((value for key, value in response_data.items() if "Pickup_Address" in key), None)
    contact_person = next((value for key, value in response_data.items() if "Contact_Person" in key), None)
    phone_number = next((value for key, value in response_data.items() if "Contact_Number" in key or "Receiver_Contact" in key), None)
    type_of_car = next((value for key, value in response_data.items() if "Vehicle_Type" in key), None)
    drop_location = next((value for key, value in response_data.items() if "Drop_off_location" in key), None)
    receiver = next((value for key, value in response_data.items() if "Receiver" in key and "Contact" not in key), None)
    receiver_contact_details = next((value for key, value in response_data.items() if "Receiver_Contact" in key), None)

    # 🚨 Do NOT extract 'Type of Assistance' from text—it will come from the image
    type_of_assist = None  

    # ✅ Log extracted values for debugging
    logging.info(f"🔍 Extracted Towing Data:\n"
                 f"📍 Pickup Location: {pickup_location}\n"
                 f"🏠 Pickup Address: {pickup_address}\n"
                 f"👤 Contact Person: {contact_person}\n"
                 f"📞 Phone Number: {phone_number}\n"
                 f"🚗 Vehicle Type: {type_of_car}\n"
                 f"📍 Drop Location: {drop_location}\n"
                 f"👤 Receiver: {receiver}\n"
                 f"📞 Receiver Contact: {receiver_contact_details}\n"
                 f"🛠️ Type of Assistance (waiting for image): {type_of_assist}")

    # ✅ Validate Required Fields (but allow `Type of Assistance` to be missing)
    missing_fields = [field for field, value in {
        "Pickup Location": pickup_location,
        "Pickup Address": pickup_address,
        "Contact Person": contact_person,
        "Phone Number": phone_number,
        "Vehicle Type": type_of_car,
        "Drop Location": drop_location,
        "Receiver": receiver,
        "Receiver Contact": receiver_contact_details
    }.items() if value is None]

    if missing_fields:
        logging.warning(f"⚠️ Missing fields: {', '.join(missing_fields)}")
        return jsonify({"error": f"Missing required fields: {', '.join(missing_fields)}"}), 400

    logging.info(f"✅ Towing Request Started - Pickup: {pickup_location}, Contact: {contact_person}, Vehicle: {type_of_car}, Drop: {drop_location}")

    # ✅ Store extracted details in session (for tracking)
    towing_sessions[session_id] = {
        "pickup_location": pickup_location,
        "pickup_address": pickup_address,
        "contact_person": contact_person,
        "phone_number": phone_number,
        "type_of_car": type_of_car,
        "type_of_assist": None,  # 🚨 This will be updated when the image is uploaded
        "drop_location": drop_location,
        "receiver": receiver,
        "receiver_contact_details": receiver_contact_details
    }

    # ✅ Step 2: Prompt User to Upload an Image for 'Type of Assistance'
    send_whatsapp_message(from_number, "📸 Please upload an image of the type of assistance needed.")

    return jsonify({"message": "Waiting for user to send image"}), 200



def process_rental_flow(response_data, message):
    """
    Step 1️⃣: Extract text from the WhatsApp Flow.
    Step 2️⃣: Prompt user for images.
    Step 3️⃣: Receive images, upload to Drive, then save rental request.
    """
    from_number = message.get("from", "")
    session_id = from_number  # Unique identifier for tracking the user session

    # ✅ Extract Text Responses from WhatsApp Flow
    customer_name_key = next((key for key in response_data.keys() if "Enter_Name" in key), None)
    vehicle_hired_key = next((key for key in response_data.keys() if "Fuel_Savers" in key), None)

    if not customer_name_key or not vehicle_hired_key:
        logging.error("❌ Missing required rental fields in response JSON.")
        return jsonify({"error": "Missing required rental details. Please complete the form."}), 400

    customer_name = response_data.get(customer_name_key)
    vehicle_hired = response_data.get(vehicle_hired_key, [])[0]  # Extract first item if list

    logging.info(f"✅ Extracted Customer: {customer_name}, Vehicle: {vehicle_hired}")

    # ✅ Store extracted details in session (for tracking)
    rental_sessions[session_id] = {
        "customer_name": customer_name,
        "vehicle_hired": vehicle_hired,
        "deposit_paid": response_data.get("deposit_paid", False),
        "completed": response_data.get("completed", False),
        "licence_id": None,
        "selfie_id": None,
        "proof_of_residence_id": None
    }

    # ✅ Step 2: Prompt User for Image Uploads
    send_whatsapp_message(from_number, "📸 Please upload your *Driver’s License*.")

    return jsonify({"message": "Waiting for user to send images"}), 200


def process_uploaded_media(message):
    """
    Handles uploaded images from the user.
    Fetches WhatsApp media URLs and uploads them to Drive.
    """
    from_number = message.get("from", "")
    media_id = message.get("media_id")

    # ✅ Determine if user is in a rental session
    if from_number in rental_sessions:
        session_data = rental_sessions[from_number]

        # ✅ Store media_id based on which image is missing
        if session_data["licence_id"] is None:
            session_data["licence_id"] = media_id
            send_whatsapp_message(from_number, "📤 *License received!* Now, send your *Selfie*.")

        elif session_data["selfie_id"] is None:
            session_data["selfie_id"] = media_id
            send_whatsapp_message(from_number, "📤 *Selfie received!* Finally, send *Proof of Residence*.")

        elif session_data["proof_of_residence_id"] is None:
            session_data["proof_of_residence_id"] = media_id
            send_whatsapp_message(from_number, "📤 *Proof received!* Processing your rental request...")

            # ✅ Step 3: Fetch media URLs
            licence_url = fetch_media_url(session_data["licence_id"])
            selfie_url = fetch_media_url(session_data["selfie_id"])
            proof_url = fetch_media_url(session_data["proof_of_residence_id"])

            # ✅ Step 4: Upload images to Google Drive
            licence_drive_link = upload_to_google_drive(licence_url, f"{session_data['customer_name']}_licence.jpg")
            selfie_drive_link = upload_to_google_drive(selfie_url, f"{session_data['customer_name']}_selfie.jpg")
            proof_drive_link = upload_to_google_drive(proof_url, f"{session_data['customer_name']}_proof.jpg")

            if not all([licence_drive_link, selfie_drive_link, proof_drive_link]):
                return jsonify({"error": "Image upload failed. Please retry."}), 500

            # ✅ Step 5: Save Rental Request
            bot_reply = save_rentals(
                session_data["customer_name"], licence_drive_link, selfie_drive_link, proof_drive_link,
                session_data["vehicle_hired"], session_data["deposit_paid"], session_data["completed"]
            )

            # ✅ Notify User & Admin
            send_whatsapp_message(from_number, bot_reply)
            send_whatsapp_message(ADMIN_NUMBER, f"🚗 New Rental Request from {session_data['customer_name']}!")

            # ✅ Clear session after completion
            del rental_sessions[from_number]

            return jsonify({"message": "Rental request processed successfully"}), 200

    # ✅ Determine if user is in a towing session
    elif from_number in towing_sessions:
        session_data = towing_sessions[from_number]

        # ✅ Save image for towing
        if session_data["type_of_assist"] is None:
            session_data["type_of_assist"] = fetch_media_url(media_id)
            session_data["type_of_assist"] = upload_to_google_drive(session_data["type_of_assist"], f"{session_data['type_of_car']}_assistance.jpg")

            send_whatsapp_message(from_number, "📤 *Image received! Processing your towing request...*")

            # ✅ Save towing request after image upload
            bot_reply = save_towing_request(
                session_data["pickup_location"], session_data["pickup_address"], session_data["contact_person"], session_data["phone_number"],
                session_data["type_of_car"], session_data["type_of_assist"], session_data["drop_location"], session_data["receiver"],
                session_data["receiver_contact_details"]
            )
            send_whatsapp_message(from_number, "✅ *Your towing request has been received!*")
            send_whatsapp_message(ADMIN_NUMBER, f"🚚 *New Towing Request from {session_data['contact_person']}!*")

            del towing_sessions[from_number]

            return jsonify({"message": "Towing request processed successfully"}), 200
        
    elif from_number in travel_sessions:
        session_data = travel_sessions[from_number]

        # ✅ Save image as 'Proof of Residence'
        if session_data["proof_of_residence_url"] is None:
            session_data["proof_of_residence_url"] = fetch_media_url(media_id)
            session_data["proof_of_residence_url"] = upload_to_google_drive(session_data["proof_of_residence_url"], f"{session_data['customer_name']}_proof.jpg")

            send_whatsapp_message(from_number, "📤 *Proof of Residence received!* Processing your travel booking...")

            # ✅ Save travel booking request after image upload
            bot_reply = save_travel_booking(
                session_data["customer_name"], session_data["proof_of_residence_url"], session_data["destination"], 
                session_data["driver"], session_data["num_of_days"], session_data["tour"], session_data["vehicle_type"]
            )
            send_whatsapp_message(from_number, "✅ *Your travel booking request has been received!*")
            send_whatsapp_message(ADMIN_NUMBER, f"🚗 *New Travel Booking from {session_data['customer_name']}!*")

            del travel_sessions[from_number]
            return jsonify({"message": "Travel booking request processed successfully"}), 200

        return jsonify({"message": "Waiting for more images"}), 200


    # ✅ If no active session is found
    logging.info(f"📷 Image received from {from_number}, but no active session detected.")
    send_whatsapp_message(from_number, "✅ Image received! However, we are not expecting an image right now.")
    return jsonify({"message": "Image received but not processed"}), 200




def process_travel_booking_flow(response_data, message):
    """
    Step 1️⃣: Extract text from WhatsApp Flow.
    Step 2️⃣: Prompt user to upload Proof of Residence.
    Step 3️⃣: Receive the image, upload it to Drive, then save travel booking request.
    """
    from_number = message.get("from", "")
    session_id = from_number  # Unique session identifier

    # ✅ Extract Data Dynamically Using Partial Key Matching
    customer_name = next((value for key, value in response_data.items() if "Enter_your_Name" in key), None)
    destination = next((value for key, value in response_data.items() if "Destination" in key), None)
    num_of_days = next((value for key, value in response_data.items() if "Number_of_days" in key), None)

    # ✅ Extract vehicle type (handle lists)
    vehicle_type = next((value[0] if isinstance(value, list) else value for key, value in response_data.items() if "Please_select_the_Vehicle_Type" in key), None)

    # ✅ Extract tour and driver choices (convert "1_No" to False)
    tour = any("Yes" in value for key, value in response_data.items() if "Would_you_like_a_tour_from_us" in key)
    driver = any("Yes" in value for key, value in response_data.items() if "Do_you_need_a_driver_from_us" in key)

    # 🚨 Do NOT extract 'Proof of Residence' from text—it will come from the image
    proof_of_residence_url = None  

    # ✅ Log extracted values for debugging
    logging.info(f"🔍 Extracted Travel Booking Data:\n"
                 f"👤 Customer: {customer_name}\n"
                 f"📍 Destination: {destination}\n"
                 f"🛞 Vehicle Type: {vehicle_type}\n"
                 f"👨‍✈️ Driver Needed: {'Yes' if driver else 'No'}\n"
                 f"📅 Number of Days: {num_of_days}\n"
                 f"🌍 Tour Included: {'Yes' if tour else 'No'}\n"
                 f"🏠 Proof of Residence (waiting for image): {proof_of_residence_url}")

    # ✅ Validate Required Fields (but allow `Proof of Residence` to be missing)
    missing_fields = [field for field, value in {
        "Customer Name": customer_name,
        "Destination": destination,
        "Number of Days": num_of_days,
        "Vehicle Type": vehicle_type
    }.items() if value is None]

    if missing_fields:
        logging.warning(f"⚠️ Missing fields: {', '.join(missing_fields)}")
        return jsonify({"error": f"Missing required fields: {', '.join(missing_fields)}"}), 400

    logging.info(f"✅ Travel Booking Started - Customer: {customer_name}, Destination: {destination}, Vehicle: {vehicle_type}")

    # ✅ Store extracted details in session (for tracking)
    travel_sessions[session_id] = {
        "customer_name": customer_name,
        "destination": destination,
        "driver": driver,
        "num_of_days": num_of_days,
        "tour": tour,
        "vehicle_type": vehicle_type,
        "proof_of_residence_url": None  # 🚨 This will be updated when the image is uploaded
    }

    # ✅ Step 2: Prompt User to Upload Proof of Residence
    send_whatsapp_message(from_number, "📸 Please upload an image of your *Proof of Residence* to complete your booking.")

    return jsonify({"message": "Waiting for user to send image"}), 200


def process_tracking_request_flow(response_data, message):
    """
    Processes tracking requests from WhatsApp Flow responses using partial key matching.
    """
    from_number = message.get("from", "")
    session_id = from_number  # Unique session identifier

    # ✅ Extract Data Dynamically Using Partial Key Matching
    user_name = next((value for key, value in response_data.items() if "Enter_your" in key and "Name" in key), None)
    address = next((value for key, value in response_data.items() if "Address" in key), None)
    phone_number = next((value for key, value in response_data.items() if "Phone_Number" in key), None)
    vehicle_make = next((value for key, value in response_data.items() if "Vehicle_Make" in key), None)
    color = next((value for key, value in response_data.items() if "Color" in key), None)
    registration_number = next((value for key, value in response_data.items() if "Registration_Number" in key), None)

    # ✅ Log extracted values for debugging
    logging.info(f"🔍 Extracted Tracking Data:\n"
                 f"👤 Name: {user_name}\n"
                 f"🏠 Address: {address}\n"
                 f"📞 Phone: {phone_number}\n"
                 f"🚗 Vehicle Make: {vehicle_make}\n"
                 f"🎨 Color: {color}\n"
                 f"🔢 Registration Number: {registration_number}")

    # ✅ Validate Required Fields
    missing_fields = [field for field, value in {
        "User Name": user_name,
        "Address": address,
        "Phone Number": phone_number,
        "Vehicle Make": vehicle_make,
        "Color": color,
        "Registration Number": registration_number
    }.items() if value is None]

    if missing_fields:
        logging.warning(f"⚠️ Missing fields: {', '.join(missing_fields)}")
        return jsonify({"error": f"Missing required tracking details: {', '.join(missing_fields)}"}), 400

    logging.info(f"✅ Tracking Request Confirmed - Name: {user_name}, Address: {address}, Phone: {phone_number}, Vehicle: {vehicle_make}, Color: {color}, Reg#: {registration_number}")

    # ✅ Save Tracking Profile
    tracking_profile = save_tracking_request(user_name, address, phone_number, vehicle_make, color, registration_number)

    # ✅ Notify Admin with Tracking Profile Details
    admin_notification = (
        f"📡 *New Tracking Profile Created!*\n\n"
        f"👤 *Name:* {user_name}\n"
        f"🏠 *Address:* {address}\n"
        f"📞 *Phone Number:* {phone_number}\n"
        f"🚗 *Vehicle:* {vehicle_make}\n"
        f"🎨 *Color:* {color}\n"
        f"🔢 *Registration Number:* {registration_number}\n"
        f"✅ *User successfully registered in the tracking system!*"
    )
    send_whatsapp_message(ADMIN_NUMBER, admin_notification)

    # ✅ Send Confirmation to User
    confirmation_message = f"✅ Profile Created Successfully!\n\n📌 Name: {user_name}\n🏠 Address: {address}\n📞 Phone: {phone_number}\n🚗 Vehicle: {vehicle_make}\n🎨 Color: {color}\n🔢 Reg#: {registration_number}\n\nThank you for registering with our tracking service!"
    
    send_whatsapp_message(from_number, confirmation_message)


    return jsonify({"message": "Tracking profile created successfully"}), 200




# def send_whatsapp_image(to_number, image_url, caption):
#     """
#     Sends an image to a WhatsApp number with a caption.
#     """
#     meta_phone_number_id = os.getenv('META_PHONE_NUMBER_ID')  # Your WhatsApp Business Number ID
#     meta_access_token = os.getenv('META_ACCESS_TOKEN')  # Your WhatsApp API Access Token

#     headers = {
#         "Authorization": f"Bearer {meta_access_token}",
#         "Content-Type": "application/json"
#     }

#     payload = {
#         "messaging_product": "whatsapp",
#         "recipient_type": "individual",
#         "to": to_number,
#         "type": "image",
#         "image": {
#             "link": image_url,
#             "caption": caption
#         }
#     }

#     url = f"https://graph.facebook.com/v18.0/{meta_phone_number_id}/messages"
    
#     response = requests.post(url, headers=headers, json=payload)

#     if response.status_code == 200:
#         logging.info(f"✅ Image sent successfully to {to_number}: {image_url}")
#     else:
#         logging.error(f"❌ Failed to send image to {to_number}. Response: {response.text}")

#     return response.json()


def send_car_images(from_number):
    """
    Sends a list of car rental images with captions to the user.
    """
    car_images = [
        {"link": "https://drive.google.com/uc?export=download&id=1GRuF6IrqIxRt7iFBwvQ9ZW-KxvCn8xZj", 
         "caption": "🚗 If you would like to rent a vehicle with APEX, please provide: \n✅ VALID driver's license.\n✅ DEPOSIT of $200 (refundable upon return of vehicle).\n✅ PROOF of residence.\n✅ IMAGE of driver (selfie 🙂)."},
        
        {"link": "https://drive.google.com/uc?export=download&id=1IcWv2AqwDZ0_Pq_p4rlN56JUWey3boAx", 
         "caption": "🚗 Our fuel savers start at $40 per day (24 hours) for a GE6 Fit and $60 for a GK3 Fit. Each day includes 150km free mileage (cumulative over multiple days). \n✅ Valid driver's license & refundable deposit of $200 required."},

        {"link": "https://drive.google.com/uc?export=download&id=17QzuSOdvVWifWQFpBhlsGk7LU_n6-V0-", 
         "caption": "🚗 Mid Range SUVs - $80/day for new generation models.\n✅ 150km free mileage per day (cumulative).\n✅ Refundable security deposit of $200."},

        {"link": "https://drive.google.com/uc?export=download&id=1fxHOsGYsuOJg4Qx1iHOOndqYZBi5v1_E", 
         "caption": "🚗 GD6 Hilux - $180/day, 200km free mileage per day (cumulative).\n🚗 GD6 Fortuner - $200/day with the same terms.\n✅ Refundable security deposit of $200."}
    ]

    # ✅ Send each image with its caption
    for image in car_images:
        send_whatsapp_file(from_number, file_url=image["link"], file_type="image", caption=image["caption"])

    return jsonify({"message": "Car images sent successfully"}), 200



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
    

def handle_image_upload(from_number, image_id):
    """
    Determines whether the image is for towing, rentals, or another use case.
    Routes it to the appropriate processing function.
    """
    # ✅ Rental Session: Send image to process rental uploads
    if from_number in rental_sessions:
        logging.info(f"📷 Image from {from_number} is for rental. Processing...")
        return process_uploaded_media({"from": from_number, "media_id": image_id})

    # ✅ Towing Session: Send image to process towing uploads
    elif from_number in towing_sessions:
        logging.info(f"📷 Image from {from_number} is for towing. Processing...")
        return process_uploaded_media({"from": from_number, "media_id": image_id})
    
    # ✅ Travel Session: Send image to process towing uploads
    elif from_number in travel_sessions:
        logging.info(f"📷 Image from {from_number} is for travel. Processing...")
        return process_uploaded_media({"from": from_number, "media_id": image_id})

    else:
        logging.info(f"📷 Image received from {from_number}, but no active session detected.")
        send_whatsapp_message(from_number, "✅ Image received! However, we are not expecting an image right now.")
        return jsonify({"message": "Image received but not processed"}), 200



def save_user_to_db(contact_number, full_name):
    """Saves or updates the user's name and number in apex_customers table."""
    conn = get_db_connection()
    if not conn:
        return

    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO apex_customers (contact_number, name)
            VALUES (%s, %s)
            ON CONFLICT (contact_number) DO UPDATE SET name = EXCLUDED.name;
        """, (contact_number, full_name))
        
        conn.commit()
        cursor.close()
        conn.close()
        logging.info(f"✅ User saved: {full_name} ({contact_number})")
    except psycopg2.Error as e:
        logging.error(f"❌ Database error saving user {contact_number}: {e}")


def send_whatsapp_file(recipient, file_url, file_type="image", caption=None):
    """
    Sends a file attachment (PDF, image, or document) via WhatsApp.
    :param recipient: WhatsApp number to send the file to
    :param file_url: Direct link to the file
    :param file_type: Type of file ("image", "document", "audio", "video")
    :param caption: Optional caption for the file
    """
    logging.info("📤 Sending file via WhatsApp...")

    # ✅ Fetch API credentials from environment variables
    phone_number_id = os.getenv("META_PHONE_NUMBER_ID")  # WhatsApp Business Number ID
    access_token = os.getenv("META_ACCESS_TOKEN")  # WhatsApp API Access Token

    if not phone_number_id or not access_token:
        logging.error("❌ Missing API credentials. Ensure META_PHONE_NUMBER_ID and META_ACCESS_TOKEN are set.")
        return

    # ✅ Validate file type
    valid_types = ["image", "document", "audio", "video"]
    if file_type not in valid_types:
        logging.error(f"❌ Invalid file type '{file_type}'. Must be one of {valid_types}.")
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

    # ✅ Add caption if provided
    if caption:
        payload[file_type]["caption"] = caption

    url = f"https://graph.facebook.com/v18.0/{phone_number_id}/messages"
    
    # ✅ Send request
    response = requests.post(url, headers=headers, json=payload)

    if response.status_code == 200:
        logging.info(f"✅ File sent successfully to {recipient}: {file_url}")
    else:
        logging.error(f"❌ Failed to send file to {recipient}. Response: {response.text}")

    return response.json()

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
        meta_access_token = os.getenv('META_ACCESS_TOKEN')  # Ensure the environment variable is set
        url = f"https://graph.facebook.com/v13.0/{meta_phone_number_id}/messages"

        try:
            data = request.json
            response = make_response("EVENT RECEIVED", 200)
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

            message = value['messages'][0]
            from_number = message['from']
            message_id = message.get('id')

             # ✅ Prevent duplicate processing
            if message_id in processed_messages:
                logging.info(f"🔄 Duplicate message detected: {message_id}. Ignoring...")
                return response  # ✅ Skips duplicate messages

            processed_messages.add(message_id)  # ✅ Mark this message as processed

            # ✅ Ignore old messages (older than 5 minutes)
            timestamp = int(message.get("timestamp", 0))
            message_time = datetime.utcfromtimestamp(timestamp)
            if datetime.utcnow() - message_time > timedelta(minutes=5):
                logging.warning(f"⏳ Ignoring old message from {message_time}")
                return response  # ✅ Prevents old messages from being processed

            incoming_message = message.get('text', {}).get('body', '').strip().lower()
            logging.info(f"📥 Message from {from_number}: {incoming_message}")
            
            # ✅ Check if the message contains an image
            if message.get('type') == 'image':
                image_id = message['image']['id']  # Get media ID
                logging.info(f"📷 Image received: {image_id}, determining action...")

                return handle_image_upload(from_number, image_id)

               
            incoming_message = message.get('text', {}).get('body', '').strip().lower()


            logging.info(f"📥 Message from {from_number}: {incoming_message}")
            
                
            if message.get('type') == "interactive":
                interactive_data = message.get('interactive', {})

                # ✅ Handle Flow Submission (nfm_reply)
                if interactive_data.get('type') == "nfm_reply":
                    flow_response = interactive_data.get('nfm_reply', {}).get('response_json')

                    if flow_response:
                        response_data = json.loads(flow_response)  # Convert string to dictionary

                        # ✅ Dynamically Determine Flow Name for Supported Flows
                        flow_name = None
                        if any("Pickup_Location" in key for key in response_data.keys()):
                            flow_name = "towing_flow"
                        elif any("Enter_Name" in key for key in response_data.keys()) and any("_SUV_" in key or "_Fuel_Savers_" in key  or "_Double_Cabs" in key or "_Mid_SUV_" in key for key in response_data.keys()):
                            flow_name = "rental_flow"

                        elif any("Destination" in key for key in response_data.keys()):
                            flow_name = "travel_flow"

                        elif any("Registration_Number" in key for key in response_data.keys()):
                            flow_name = "tracking_flow"

                        elif any("user_name" in key  for key in response_data.keys()): 
                            user_name = response_data.get("user_name")
                            
                            if user_name:  # ✅ If name is provided, store it
                                save_user_to_db(from_number, user_name)

                                bot_reply = f"Awesome, {user_name}! How can I assist you today?"
                                send_whatsapp_message(from_number, bot_reply)

                            else:  # ✅ If no name was entered, ask again
                                bot_reply = "I didn't catch your name. Please enter your full name so we can assist you better."
                                send_whatsapp_message(from_number, bot_reply)
                        

                        if not flow_name:
                            logging.warning("⚠️ Unable to determine Flow Name from response JSON.")
                            return jsonify({"error": "Flow Name missing or unrecognized"}), 400

                        # ✅ Process the Response Based on Flow Name
                        if flow_name == "towing_flow":
                            logging.info(f"✅ Detected Towing Flow - Processing Towing Request")
                            return process_towing_flow(response_data, message)
                        
                        elif flow_name == "rental_flow":
                            logging.info(f"✅ Detected Rental Flow - Processing Rental Request")
                            return process_rental_flow(response_data, message)
                        elif flow_name == "travel_flow":
                            logging.info(f"✅ Detected Travel Booking Flow - Processing Travel Booking")
                            return process_travel_booking_flow(response_data, message)
                        elif flow_name == "tracking_flow":
                            logging.info(f"✅ Detected Tracking Flow - Processing Tracking Request")
                            return process_tracking_request_flow(response_data, message)

                        # 🚨 If Flow Name is Unrecognized
                        logging.warning(f"⚠️ Unrecognized Flow Name: {flow_name}")
                        return jsonify({"error": "Unknown flow name"}), 400

                    else:
                        logging.warning("⚠️ Flow response JSON is missing.")
                        return jsonify({"error": "Flow response JSON missing"}), 400

                return jsonify({"message": "Message processed"}), 200


            elif from_number not in user_data or not user_data[from_number].get("file_sent"):
                file_url = "https://drive.google.com/uc?export=download&id=1pN06Ao0ZQEgxsS8OX6MuierKMR_WdDjH"
                caption = "Apex Travel\n" 

                # ✅ Send video first
                send_whatsapp_file(from_number, file_url, file_type="video", caption=caption)

                # ✅ Then trigger the WhatsApp Flow
                bot_reply = "Welcome to the Apex Experience! I'd love to know you, what is your name? Please enter your name on this form"
                flow_cta = "Click to Enter Name"
                flow_name = "name_capture_flow"
                trigger_whatsapp_flow(from_number, bot_reply, flow_cta, flow_name)

                user_data[from_number] = {"file_sent": True}  # ✅ Mark file as sent

           
            elif "towing request" in incoming_message.lower():
                bot_reply = "🚗 Need towing assistance? Click below to start the process."
                flow_cta = "Request Towing"
                flow_name = "towing_flow"  # ✅ Use the correct flow name for towing

                # ✅ Trigger WhatsApp Flow for Towing Request
                flow_response = trigger_whatsapp_flow(from_number, bot_reply, flow_cta, flow_name)

                logging.debug(f"🔍 Towing Flow Trigger Debug - User: {from_number}, Response: {flow_response}")
                return jsonify({"message": "Towing flow triggered"}), 200


            elif "tracking vehicle" in incoming_message.lower():
                bot_reply = "📦 Want to track your vehicle? Click below to get started!."
                flow_cta = "Tracking Request"
                flow_name = "tracking_flow"  

                # ✅ Trigger WhatsApp Flow for Tracking Request
                flow_response = trigger_whatsapp_flow(from_number, bot_reply, flow_cta, flow_name)

                logging.debug(f"🔍 Tracking Flow Trigger Debug - User: {from_number}, Response: {flow_response}")
                return jsonify({"message": "Tracking flow triggered"}), 200
            

            elif "travel booking" in incoming_message.lower():
                bot_reply = "Ready to start your trip? Click below to start the process to hire a vehicle."
                flow_cta = "Start Travel Hiring"
                flow_name = "travel_flow"  # ✅ Use the correct flow name for travel

                # ✅ Trigger WhatsApp Flow for Travel Booking Request
                flow_response = trigger_whatsapp_flow(from_number, bot_reply, flow_cta, flow_name)

                logging.debug(f"🔍 Travel Booking Flow Trigger Debug - User: {from_number}, Response: {flow_response}")
                return jsonify({"message": "Travel booking flow triggered"}), 200
            
            elif "show images" in incoming_message.lower():
                return send_car_images(from_number)

            

            elif "rent a car" in incoming_message.lower() or "vehicle rental" in incoming_message.lower():
                bot_reply = "🚘 Need a rental car? Click below to start the booking process."
                flow_cta = "Start Rental Booking"
                flow_name = "rental_flow" 

                # ✅ Trigger WhatsApp Flow for Rental Request
                flow_response = trigger_whatsapp_flow(from_number, bot_reply, flow_cta, flow_name)

                logging.debug(f"🔍 Rental Flow Trigger Debug - User: {from_number}, Response: {flow_response}")
                return jsonify({"message": "Rental flow triggered"}), 200
            



                    # ⭐ Handling AI Responses
            else:
                bot_reply = query_openai_model(incoming_message, from_number)
                log_conversation(from_number, incoming_message, bot_reply, "processed")
                send_whatsapp_message(from_number, bot_reply)
              
                
                                

                # Detect if the message is a greeting or farewell
                if any(greeting in incoming_message.lower() for greeting in ["hello", "hi", "good morning","hey", "howdy", "good evening", "greetings"]):
                    # Append name to greeting
                    bot_reply = f"Hello {get_customer_name(from_number)}! " + query_openai_model(
                        incoming_message, from_number
                    )
                elif any(farewell in incoming_message.lower() for farewell in ["bye", "goodbye", "see you", "take care"]):
                    # Append name to farewell
                    bot_reply = query_openai_model(
                        incoming_message, from_number
                    ) + f" Goodbye {get_customer_name(from_number)}!"
                else:
                    
                    bot_reply = query_openai_model(incoming_message, from_number)
            
                log_conversation(from_number, incoming_message, bot_reply, "processed")

              
                send_whatsapp_message(from_number, bot_reply)
                return "Message processed.", 200


            # ✅ Send response via WhatsApp Meta API
            headers = {
                "Authorization": f"Bearer {meta_access_token}",
                "Content-Type": "application/json"
            }
            payload = {
                "messaging_product": "whatsapp",
                "to": from_number,
                "type": "text",
                "text": {"body": bot_reply}
            }
            response = requests.post(url, headers=headers, json=payload)

            if response.status_code == 200:
                logging.info(f"✅ Message Sent Successfully to {from_number}")
            else:
                logging.error(f"❌ Error Sending Message: {response.text}")

            return response 
        
        except Exception as e:
            logging.error(f"❌ Error Processing Webhook: {e}")
            logging.error(traceback.format_exc())



# def check_inactivity(from_number):
#     """
#     Check if the user has been inactive for more than 30 minutes and update session memory if needed.
#     """
#     conn = get_db_connection()
#     if not conn:
#         return

#     try:
#         cursor = conn.cursor()

#         # Retrieve the last message timestamp
#         cursor.execute('''
#             SELECT timestamp FROM conversations
#             WHERE from_number = %s
#             ORDER BY timestamp DESC
#             LIMIT 1
#         ''', (from_number,))
#         last_activity = cursor.fetchone()

#         if not last_activity:
#             logging.info(f"🔍 No previous activity found for {from_number}. Skipping inactivity check.")
#             return

#         last_activity_time = last_activity[0]
#         inactive_duration = datetime.now() - last_activity_time

#         # If user has been inactive for more than 30 minutes, summarize session
#         if inactive_duration > timedelta(minutes=30):
#             session_summary = summarize_session(from_number)
#             save_session_to_db(from_number, session_summary)
#             logging.info(f"🔄 Session for {from_number} summarized due to inactivity.")

#     except psycopg2.Error as e:
#         logging.error(f"Database error while checking inactivity: {e}")

#     finally:
#         cursor.close()
#         conn.close()

 


# def send_periodic_updates():
#     """Send periodic updates to the admin."""
#     conn = get_db_connection()
#     if not conn:
#         return

#     try:
#         cursor = conn.cursor()
#         cursor.execute('SELECT from_number, message, bot_reply FROM restaurant WHERE reported = 0')
#         restaurant = cursor.fetchall()

#         if restaurant:
#             update_message = "Hourly Update:\n\n"
#             for conv in restaurant:
#                 from_number, message, bot_reply = conv
#                 update_message += f"From: {from_number}\nMessage: {message}\nBot Reply: {bot_reply}\n\n"

#             send_whatsapp_message(ADMIN_NUMBER, update_message)

#             cursor.execute('UPDATE restaurant SET reported = 1 WHERE reported = 0')
#             conn.commit()

#     except psycopg2.Error as e:
#         logging.error(f"Database error: {e}")

#     finally:
#         cursor.close()
#         conn.close()

# schedule.every().hour.do(send_periodic_updates)

# Schedule the proactive messaging function
#schedule.every(2).minutes.do(send_intro_to_new_customers)

#Schedule Bulk Messaging 
#schedule.every().day.at("09:00").do(send_available_vehicles )
#schedule.every(1).minute.do(send_available_vehicles )


def run_scheduler():
    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == '__main__':
    scheduler_thread = threading.Thread(target=run_scheduler)
    scheduler_thread.daemon = True
    scheduler_thread.start()
    app.run(debug=False, use_reloader=False,host="0.0.0.0", port=5000)
