import os
import random
import smtplib
import sqlite3
from flask import Flask, request, redirect, url_for, render_template_string, flash
from werkzeug.utils import secure_filename
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "your_secure_secret_key")  # Use a secure key

# Configuration for file uploads
UPLOAD_FOLDER = 'uploads'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
ALLOWED_EXTENSIONS = {'txt'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Initialize a simple SQLite database to store user email and their uploaded file path
def init_db():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
                    email TEXT PRIMARY KEY,
                    file_path TEXT
                )''')
    conn.commit()
    conn.close()

init_db()

# Function to read clippings from a given file path
def read_clippings(file_path, encoding="utf-8"):
    try:
        with open(file_path, 'r', encoding=encoding) as file:
            clippings = file.read().split("==========\n")
        return clippings if clippings else []
    except Exception as e:
        print(f"Error reading clippings file {file_path}: {e}")
        return []

# Function to format clippings for email display
def separate_clipping(clipping):
    parts = clipping.split("\n")
    book_details = parts[0].strip()
    highlight_text = parts[-2].strip()
    return f"<b>{book_details}</b><br/><br/><i>\"{highlight_text}\"</i><br/><br/>"

# Function to select random clippings
def select_random_clippings(clippings, num_clippings=5):
    if not clippings:
        return ["No clippings available."]
    
    selected_clippings = []
    for _ in range(min(num_clippings, len(clippings))):
        random_index = random.randint(0, len(clippings) - 1)
        formatted_clipping = separate_clipping(clippings[random_index])
        selected_clippings.append(formatted_clipping)
    return selected_clippings

# Function to send an email
def send_email(to_email, message):
    SENDER_EMAIL = os.getenv("SENDER_EMAIL")
    SENDER_PASSWORD = os.getenv("SENDER_PASSWORD")

    if not SENDER_EMAIL or not SENDER_PASSWORD:
        print("Email credentials not set in environment variables.")
        return

    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)

        msg = MIMEMultipart()
        msg['From'] = SENDER_EMAIL
        msg['To'] = to_email
        msg['Subject'] = "Your Daily Kindle Clippings"
        msg.attach(MIMEText(message, 'html'))

        server.sendmail(SENDER_EMAIL, to_email, msg.as_string())
        server.quit()

        print(f"Email sent successfully to {to_email}")

    except smtplib.SMTPAuthenticationError as e:
        print(f"SMTP Authentication Error: {e}")
    except Exception as e:
        print(f"Error sending email to {to_email}: {e}")

# Flask route to upload a Kindle clippings file and associate it with an email
@app.route('/', methods=['GET', 'POST'])
def upload_file():
    if request.method == 'POST':
        if 'clippings' not in request.files:
            flash('No file selected.')
            return redirect(request.url)
        
        file = request.files['clippings']
        email = request.form.get('email')

        if file.filename == '':
            flash('No file selected.')
            return redirect(request.url)

        if file and allowed_file(file.filename) and email:
            filename = secure_filename(file.filename)
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(file_path)

            # Save the user's email and file path in the database
            conn = sqlite3.connect('users.db')
            c = conn.cursor()
            c.execute("INSERT OR REPLACE INTO users (email, file_path) VALUES (?, ?)", (email, file_path))
            conn.commit()
            conn.close()

            flash('File successfully uploaded and email saved.')
            return redirect(url_for('upload_file'))

    return render_template_string('''
    <!doctype html>
    <html>
      <head>
        <title>Upload Kindle Clippings</title>
      </head>
      <body>
        {% with messages = get_flashed_messages() %}
          {% if messages %}
            <ul>
              {% for message in messages %}
                <li>{{ message }}</li>
              {% endfor %}
            </ul>
          {% endif %}
        {% endwith %}
        <h1>Upload Your Kindle Clippings and Enter Your Email</h1>
        <form method="post" enctype="multipart/form-data">
          <input type="file" name="clippings" required>
          <br/><br/>
          <input type="email" name="email" placeholder="Your Email" required>
          <br/><br/>
          <input type="submit" value="Upload">
        </form>
      </body>
    </html>
    ''')

# Function to send daily emails, ensuring each user gets clippings from their own file
def send_daily_emails():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("SELECT email, file_path FROM users")
    users = c.fetchall()
    conn.close()

    for email, file_path in users:
        try:
            clippings = read_clippings(file_path)
            if not clippings:
                print(f"No clippings found for {email}. Skipping email.")
                continue

            selected_clippings = select_random_clippings(clippings)
            message = "<br/>".join(selected_clippings)
            send_email(email, message)

        except Exception as e:
            print(f"Failed to send email to {email}: {e}")

# Schedule the daily email task
scheduler = BackgroundScheduler()
scheduler.add_job(func=send_daily_emails, trigger="interval", hours=24)
scheduler.start()

if __name__ == "__main__":
    app.run(debug=True, host='0.0.0.0', port=5000)
