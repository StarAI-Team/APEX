from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import psycopg2
from flask_bcrypt import Bcrypt
from main import send_whatsapp_message, trigger_whatsapp_flow
import pandas as pd
import os
import logging
from dotenv import load_dotenv
import requests
from main import get_db_connection
from dotenv import load_dotenv
from threading import Thread




# Load environment variables from .env file
load_dotenv()

# Configure Logging
logging.basicConfig(
    filename="agent.log",
    level=logging.INFO,  # ✅ Ignores DEBUG logs, only logs INFO and above
    format="%(asctime)s - %(levelname)s - %(message)s"
)




app = Flask(__name__)
app.secret_key = '14637453g55snd2437s.6e4w1_e)'  
bcrypt = Bcrypt(app)




# Admin number to send periodic updates
ADMIN_NUMBER = os.getenv('ADMIN_NUMBER')


def notify_user(contact_number, message, flow_cta, flow_id):
    """
    Trigger a WhatsApp Flow notification to the user.
    """
    try:
        # Remove 'whatsapp:' prefix if already present
        if contact_number.startswith('whatsapp:'):
            contact_number = contact_number.replace('whatsapp:', '')

        # Trigger the WhatsApp flow instead of sending a regular message
        result = trigger_whatsapp_flow(contact_number, message, flow_cta, flow_id)
        logging.info(f"Flow triggered for {contact_number}: {message} | Result: {result}")
    except Exception as e:
        logging.error(f"Error triggering WhatsApp flow for {contact_number}: {e}")




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
            stored_hash = row[0]
            logging.debug(f"Debug: Stored password hash: {stored_hash}")
            
            if bcrypt.check_password_hash(stored_hash, password):
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
        username = request.form.get('username')
        password = request.form.get('password')
        logging.debug(f"Debug: Received login request for username: {username}")

        if verify_password(username, password):
            logging.debug("Debug: Authentication successful, setting session and redirecting")
            session['user'] = username
            session.modified = True  # Ensure session updates
            return redirect(url_for('dashboard'))
        else:
            logging.error("Authentication failed, rendering login page with error")
            return render_template('index.html', error="Invalid username or password")

    logging.debug("Debug: Rendering login page")
    return render_template('index.html')




@app.route('/vehicles')
def vehicles():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT vehicle_id, rental_price, free_mileage, deposit, status, car_type FROM vehicles")
    vehicles = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('vehicles.html', vehicles=vehicles)

@app.route('/add_vehicle', methods=['POST'])
def add_vehicle():
    car_type = request.form['car_type']
    rental_price = request.form['rental_price']
    free_mileage = request.form['free_mileage']
    deposit = request.form['deposit']
    status = request.form['status']
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO vehicles (car_type, rental_price, free_mileage, deposit, status) VALUES (%s, %s, %s, %s, %s)", (car_type, rental_price, free_mileage, deposit, status))
    conn.commit()
    cursor.close()
    conn.close()
    return redirect(url_for('vehicles'))

@app.route('/update_vehicle_status/<int:vehicle_id>', methods=['POST'])
def update_vehicle_status(vehicle_id):
    status = request.form['status']
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE vehicles SET status = %s WHERE vehicle_id = %s", (status, vehicle_id))
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({'success': True})


@app.route('/customers')
def customers():
    """Fetch customer details from the database and display them"""
    conn = get_db_connection()
    if not conn:
        return "Database connection failed"

    cursor = conn.cursor()
    cursor.execute("SELECT from_number FROM threads")
    customers = cursor.fetchall()
    cursor.close()
    conn.close()
    
    return render_template('customers.html', customers=customers)



from threading import Thread, Lock

# Dictionary to store counts
stats = {
    'total_customers': 0,
    'total_rentals': 0,
    'total_towing': 0,
    'total_tracking': 0,
    'total_travels': 0,
    'total_conversations': 0,
    'total_vehicles': 0
}

# Lock for thread safety
lock = Lock()

def count_customers():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM threads")
    count = cursor.fetchone()[0]
    cursor.close()
    conn.close()

    # Update the stats dictionary safely
    with lock:
        stats['total_customers'] = count


def count_vehicles():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM vehicles")
    count = cursor.fetchone()[0]
    cursor.close()
    conn.close()

    # Update the stats dictionary safely
    with lock:
        stats['total_vehicles'] = count


def count_services(service_type, key):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(f"SELECT COUNT(*) FROM services WHERE service = %s", (service_type,))
    count = cursor.fetchone()[0]
    cursor.close()
    conn.close()

    # Update the stats dictionary safely
    with lock:
        stats[key] = count

def count_conversations():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM conversations")
    count = cursor.fetchone()[0]
    cursor.close()
    conn.close()

    # Update the stats dictionary safely
    with lock:
        stats['total_conversations'] = count

def update_stats():
    customer_thread = Thread(target=count_customers)
    rental_thread = Thread(target=count_services, args=('Rental', 'total_rentals'))
    vehicle_thread = Thread(target=count_vehicles)
    towing_thread = Thread(target=count_services, args=('Towing', 'total_towing'))
    tracking_thread = Thread(target=count_services, args=('Tracking', 'total_tracking'))
    travel_thread = Thread(target=count_services, args=('Travel', 'total_travels'))
    conversation_thread = Thread(target=count_conversations)

    threads = [customer_thread, vehicle_thread, rental_thread, towing_thread, tracking_thread, travel_thread, conversation_thread]

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join()

@app.route('/dashboard')
def dashboard():
    update_stats()
    return render_template('dashboard.html', **stats)



@app.route('/waitlist')
def waitlist():
    """
    Display the waitlist of users who are waiting for a vehicle.
    """
    conn = get_db_connection()
    if not conn:
        return "Database connection failed"

    cursor = conn.cursor()
    cursor.execute("SELECT id, user_name, car_type, request_time, status, notification_sent FROM waitlist")
    waitlist = cursor.fetchall()
    cursor.close()
    conn.close()

    return render_template('waitlist.html', waitlist=waitlist)

@app.route('/remove_from_waitlist/<int:waitlist_id>', methods=['POST'])
def remove_from_waitlist(waitlist_id):
    """
    Remove a user from the waitlist.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("DELETE FROM waitlist WHERE id = %s", (waitlist_id,))
        conn.commit()

        cursor.close()
        conn.close()
        logging.info(f"Successfully removed waitlist entry with ID: {waitlist_id}")
        return jsonify({"success": True, "message": "Removed from waitlist"})
    except Exception as e:
        logging.error(f"Error removing from waitlist: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route('/rentals')
def rentals():
    """Fetch rental records"""
    conn = get_db_connection()
    if not conn:
        return "Database connection failed"

    cursor = conn.cursor()

    # Corrected query to match the right order of columns
    cursor.execute("""
        SELECT id, name, details, image_link, contact, created_at, ref_number, pop_link
        FROM services 
        WHERE service = 'Rental'
    """)
    rentals = cursor.fetchall()
    cursor.close()
    conn.close()

    # Log the fetched data to verify
    logging.info(f"Fetched rentals: {rentals}")

    return render_template('rentals.html', rentals=rentals)



@app.route('/mark_rental_done/<int:rental_id>', methods=['POST'])
def mark_rental_done(rental_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # ✅ Fetch rental details using the correct column name name instead of customername
        cursor.execute("SELECT name, contact FROM services WHERE id = %s", (rental_id,))
        rental = cursor.fetchone()

        if not rental:
            cursor.close()
            conn.close()
            return jsonify({"success": False, "message": "Rental not found"}), 404

        # ✅ Update details column to "DONE"
        cursor.execute("UPDATE services SET details = 'DONE' WHERE id = %s", (rental_id,))
        conn.commit()

        cursor.close()
        conn.close()

        # Log for debugging
        logging.info(f"Rental {rental_id} marked as done for {rental[0]}")

        # ✅ Send WhatsApp notification using the contact number
        notify_user(
            rental[1], 
            f"Thank you {rental[0]}! Your rental is done now. We'd love your feedback!", 
            "Enter Rating",  
            "..."  
        )


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
    cursor.execute("SELECT id, name, contact, details, image_link, ref_number, pop_link FROM services WHERE service = 'Towing'")
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
    # Adjusting the query to match the expected output structure
    cursor.execute("SELECT id, name, details, contact, image_link, ref_number, pop_link FROM services WHERE service = 'Tracking'")
    tracking_records = cursor.fetchall()

    # Print to check the data format
    print("Tracking Records: ", tracking_records)

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
    cursor.execute("SELECT id, name, contact, details, image_link, ref_number, pop_link FROM services WHERE service = 'Travel'")
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
    cursor.execute("SELECT conversation_id, from_number, message, bot_reply, timestamp FROM conversations ORDER BY timestamp DESC LIMIT 50")
    conversations = cursor.fetchall()
    cursor.close()
    conn.close()

    return render_template('conversations.html', conversations=conversations)

@app.route('/settings')
def settings():
    """Settings page for user preferences and configuration"""
    return render_template('settings.html')



@app.route('/logout')
def logout():
    """Log out the user by clearing the session"""
    session.clear()
    logging.info("User logged out successfully")
    return redirect(url_for('login'))


if __name__ == "__main__":
    app.run(host="0.0.0.0",debug=True, port=8000) 
  
