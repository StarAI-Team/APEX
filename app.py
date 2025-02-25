from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import psycopg2
from flask_bcrypt import Bcrypt
from main import send_whatsapp_message,get_customer_name, trigger_whatsapp_flow
import pandas as pd
import os
import logging
from dotenv import load_dotenv
import requests
from main import get_db_connection
from dotenv import load_dotenv




# Load environment variables from .env file
load_dotenv()

# Configure Logging
logging.basicConfig(
    filename="app.log",  
    level=logging.DEBUG,  
    format="%(asctime)s - %(levelname)s - %(message)s"
)



app = Flask(__name__)
app.secret_key = '14637453g55snd2437s.6e4w1_e)'  
bcrypt = Bcrypt(app)




# Admin number to send periodic updates
ADMIN_NUMBER = os.getenv('ADMIN_NUMBER')


def notify_user(contact_number, message):
    """
    Send a WhatsApp notification to the user.
    """
    try:
        # Remove 'whatsapp:' prefix if already present
        if contact_number.startswith('whatsapp:'):
            contact_number = contact_number.replace('whatsapp:', '')

        # Send the WhatsApp message
        send_whatsapp_message(contact_number, message)  
        logging.info(f"Notification sent to {contact_number}: {message}")
    except Exception as e:
        logging.error(f"Error sending notification to {contact_number}: {e}")



def verify_password(username, password):
    logging.info(f"Debug: Verifying password for user: {username}")

    conn = get_db_connection()
    if not conn:
        logging.debug("Debug: Database connection is None, returning False")
        return False

    try:
        cursor = conn.cursor()
        logging.info("Debug: Executing query to fetch password hash")
        cursor.execute("SELECT password_hash FROM admin_users WHERE username = %s", (username,))
        row = cursor.fetchone()

        if row:
            logging.info("Debug: User found in database, verifying password")
            if bcrypt.check_password_hash(row[0], password):
                logging.info("Debug: Password verification successful")
                return True
            else:
                logging.debug("Debug: Password verification failed")
        else:
            logging.info("Debug: No user found with the given username")

    except psycopg2.Error as e:
        logging.debug(f"Debug: Database error while verifying password: {e}")

    finally:
        cursor.close()
        conn.close()
        logging.debug("Debug: Database connection closed")
    logging.debug("Debug: Returning False from verify_password")
    return False


@app.route('/', methods=['GET', 'POST'])
def login():
    """
    Login route to authenticate users.
    """
    logging.info("Debug: Login route accessed")

    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        logging.debug(f"Debug: Received login request for username: {username}")

        if verify_password(username, password):
            logging.debug("Debug: Authentication successful, setting session and redirecting")
            session['user'] = username
            return redirect(url_for('dashboard'))
        else:
            logging.error("Authentication failed, rendering login page with error")
            return render_template('index.html', error="Invalid username or password")

    logging.debug("Debug: Rendering login page")
    return render_template('index.html')


@app.route('/dashboard')
def dashboard():
    """Fetch statistics for the dashboard"""
    conn = get_db_connection()
    if not conn:
        return "Database connection failed"

    cursor = conn.cursor()

    # Fetch statistics
    cursor.execute("SELECT COUNT(*) FROM apex_customers")
    total_customers = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM rentals")
    total_rentals = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM towing")
    total_towing = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM tracking")
    total_tracking = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM travels")
    total_travels = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM conversations")
    total_conversations = cursor.fetchone()[0]

    cursor.close()
    conn.close()

    # Pass data to the template
    return render_template('dashboard.html', total_customers=total_customers, total_rentals=total_rentals,
                           total_towing=total_towing, total_tracking=total_tracking, total_travels=total_travels,
                           total_conversations=total_conversations)


@app.route('/customers')
def customers():
    """Fetch customer details from the database and display them"""
    conn = get_db_connection()
    if not conn:
        return "Database connection failed"

    cursor = conn.cursor()
    cursor.execute("SELECT id, name, contact_number FROM apex_customers")
    customers = cursor.fetchall()
    cursor.close()
    conn.close()
    
    return render_template('customers.html', customers=customers)



@app.route('/rentals')
def rentals():
    """Fetch rental records"""
    conn = get_db_connection()
    if not conn:
        return "Database connection failed"

    cursor = conn.cursor()
    cursor.execute("SELECT id, customername, vehiclehired, depositpaid, licence, selfie, proofofresidence FROM rentals")
    rentals = cursor.fetchall()
    cursor.close()
    conn.close()

    return render_template('rentals.html', rentals=rentals)


@app.route('/mark_rental_done/<int:rental_id>', methods=['POST'])
def mark_rental_done(rental_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # ✅ Fetch rental details using `from_number`
        cursor.execute("SELECT customername, from_number FROM rentals WHERE id = %s", (rental_id,))
        rental = cursor.fetchone()

        if not rental:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "Rental not found"}), 404

        # ✅ Update `completed` column (Boolean value)
        cursor.execute("UPDATE rentals SET completed = TRUE WHERE id = %s", (rental_id,))
        conn.commit()

        cursor.close()
        conn.close()

        # Log for debugging
        logging.info(f"Rental {rental_id} marked as done for {rental[0]}")

        # ✅ Send WhatsApp Flow using `from_number`
        notify_user(rental[1])  # `rental[1]` is `from_number`

        return jsonify({"success": True, "message": "Rental marked as done and user notified"})

    except Exception as e:
        logging.error(f"Error in mark_rental_done: {e}")
        return jsonify({"success": False, "message": f"Internal Server Error: {str(e)}"}), 500


@app.route('/towing')
def towing():
    """Fetch towing records"""
    conn = get_db_connection()
    if not conn:
        return "Database connection failed"

    cursor = conn.cursor()
    cursor.execute("SELECT id, contactperson, phonenumber, typeofcar, droplocation FROM towing")
    towing = cursor.fetchall()
    cursor.close()
    conn.close()

    return render_template('towing.html', towing=towing)

@app.route('/tracking')
def tracking():
    """Fetch tracking records"""
    conn = get_db_connection()
    if not conn:
        return "Database connection failed"

    cursor = conn.cursor()
    cursor.execute("SELECT id, customername, phonenumber, vehiclemake, colour, regnumber, address FROM tracking")
    tracking_records = cursor.fetchall()
    cursor.close()
    conn.close()

    return render_template('tracking.html', tracking=tracking_records)


@app.route('/travels')
def travels():
    """Fetch travel bookings"""
    conn = get_db_connection()
    if not conn:
        return "Database connection failed"

    cursor = conn.cursor()
    cursor.execute("SELECT id, customername, destination, driver, vehicle_type FROM travels")
    travels = cursor.fetchall()
    cursor.close()
    conn.close()

    return render_template('travels.html', travels=travels)

@app.route('/conversations')
def conversations():
    """Fetch chat records"""
    conn = get_db_connection()
    if not conn:
        return "Database connection failed"

    cursor = conn.cursor()
    cursor.execute("SELECT id, from_number, message, bot_reply, timestamp FROM conversations ORDER BY timestamp DESC LIMIT 50")
    conversations = cursor.fetchall()
    cursor.close()
    conn.close()

    return render_template('conversations.html', conversations=conversations)

if __name__ == "__main__":
    app.run(host="0.0.0.0",debug=True, port=8000) 
  