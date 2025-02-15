import os
import sqlite3
import datetime
import random
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from flask import Flask, request, redirect, url_for, flash, render_template_string, send_from_directory
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_bcrypt import Bcrypt
from werkzeug.utils import secure_filename
from apscheduler.schedulers.background import BackgroundScheduler

# --------------------------
# App & Configuration Setup
# --------------------------
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "your_secure_secret_key")
bcrypt = Bcrypt(app)

# Flask-Login Setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

# Uploads Configuration
UPLOAD_FOLDER = 'uploads'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
ALLOWED_EXTENSIONS = {'txt'}

# Email Credentials (Gmail SMTP)
SENDER_EMAIL = os.getenv("SENDER_EMAIL", "example@gmail.com")
SENDER_PASSWORD = os.getenv("SENDER_PASSWORD", "your_app_password")

# --------------------------
# Database Initialization
# --------------------------
def init_db():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    # Users table: stores email, hashed password, chosen frequency, and last_sent timestamp
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            frequency TEXT NOT NULL DEFAULT 'daily',
            last_sent TEXT
        )
    ''')
    # Clippings table: each uploaded file belongs to a user
    c.execute('''
        CREATE TABLE IF NOT EXISTS clippings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            file_path TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# --------------------------
# User Model & Loader
# --------------------------
class User(UserMixin):
    def __init__(self, id, email, frequency='daily', last_sent=None):
        self.id = id
        self.email = email
        self.frequency = frequency
        self.last_sent = last_sent

@login_manager.user_loader
def load_user(user_id):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("SELECT id, email, frequency, last_sent FROM users WHERE id = ?", (user_id,))
    user = c.fetchone()
    conn.close()
    if user:
        return User(user[0], user[1], user[2], user[3])
    return None

# --------------------------
# Clippings Parsing Helpers
# --------------------------
def read_clippings_from_file(file_path, encoding="utf-8"):
    with open(file_path, 'r', encoding=encoding) as f:
        return f.read().split("==========\n")

def separate_clipping(clipping):
    # Split into non-empty lines
    lines = [line.strip() for line in clipping.strip().split("\n") if line.strip()]
    if len(lines) < 2:
        return ""
    book_details = lines[0]
    highlight_text = lines[-1]  # Use the last non-empty line as the highlight text
    return (
        f"<div style='margin-bottom:20px; padding-bottom:10px; border-bottom:1px solid #ddd;'>"
        f"<strong style='font-size:18px; color:#333;'>{book_details}</strong><br/><br/>"
        f"<em style='font-size:16px; color:#555;'>&ldquo;{highlight_text}&rdquo;</em>"
        f"</div>"
    )

def generate_email_content(user_id, num_clippings=5):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("SELECT file_path FROM clippings WHERE user_id = ?", (user_id,))
    files = c.fetchall()
    conn.close()
    content_parts = []
    for file_tuple in files:
        file_path = file_tuple[0]
        try:
            clippings = read_clippings_from_file(file_path)
            clippings = [clip for clip in clippings if clip.strip()]
            if clippings:
                selected = random.sample(clippings, min(num_clippings, len(clippings)))
                formatted = "".join([separate_clipping(clip) for clip in selected])
                content_parts.append(formatted)
        except Exception as e:
            content_parts.append(f"<p>Error reading {file_path}: {e}</p>")
    return "<br>".join(content_parts) if content_parts else "<p>No clippings uploaded.</p>"

# --------------------------
# Email Sending Function (smtplib)
# --------------------------
def send_email_to_user(user, email_body):
    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        msg = MIMEMultipart()
        msg['From'] = SENDER_EMAIL
        msg['To'] = user.email
        msg['Subject'] = "Your Kindle Clippings"
        msg.attach(MIMEText(email_body, 'html'))
        server.sendmail(SENDER_EMAIL, user.email, msg.as_string())
        server.quit()
        print(f"Email sent to {user.email}")
        return True
    except Exception as e:
        print(f"Error sending email to {user.email}: {e}")
        return False

# --------------------------
# APScheduler: Scheduled Email Sending
# --------------------------
def scheduled_email_job():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("SELECT id, email, frequency, last_sent FROM users")
    users = c.fetchall()
    conn.close()
    now = datetime.datetime.now()
    for u in users:
        user = User(u[0], u[1], u[2], u[3])
        send_email_flag = False
        if user.frequency == 'daily':
            if user.last_sent:
                last = datetime.datetime.fromisoformat(user.last_sent)
                if last.date() < now.date():
                    send_email_flag = True
            else:
                send_email_flag = True
        elif user.frequency == 'weekly':
            if now.weekday() == 0:  # Monday
                if user.last_sent:
                    last = datetime.datetime.fromisoformat(user.last_sent)
                    if last.isocalendar()[1] < now.isocalendar()[1]:
                        send_email_flag = True
                else:
                    send_email_flag = True
        elif user.frequency == 'monthly':
            if now.day == 1:
                if user.last_sent:
                    last = datetime.datetime.fromisoformat(user.last_sent)
                    if last.month < now.month or last.year < now.year:
                        send_email_flag = True
                else:
                    send_email_flag = True
        if send_email_flag:
            content = generate_email_content(user.id)
            if send_email_to_user(user, content):
                conn = sqlite3.connect('users.db')
                c = conn.cursor()
                c.execute("UPDATE users SET last_sent = ? WHERE id = ?", (now.isoformat(), user.id))
                conn.commit()
                conn.close()

scheduler = BackgroundScheduler()
scheduler.add_job(func=scheduled_email_job, trigger="interval", hours=1)
scheduler.start()

# --------------------------
# Routes with Tailwind CDN Styling
# --------------------------

# Home Page
@app.route('/')
def home():
    return render_template_string('''
    <!DOCTYPE html>
    <html lang="en">
      <head>
        <title>Kindle Clippings</title>
        <script src="https://cdn.tailwindcss.com"></script>
      </head>
      <body class="bg-gray-100 flex flex-col items-center justify-center h-screen">
        <div class="p-8 bg-white rounded-lg shadow-lg text-center">
          <h1 class="text-4xl font-bold text-gray-700">📖 Kindle Clippings</h1>
          <p class="text-gray-500 mt-2">Upload your Kindle highlights and receive them by email at your chosen frequency.</p>
          <div class="mt-5 space-x-4">
            <a href="/signup" class="px-6 py-3 bg-blue-600 text-white rounded-lg shadow-md hover:bg-blue-700">Sign Up</a>
            <a href="/login" class="px-6 py-3 bg-gray-600 text-white rounded-lg shadow-md hover:bg-gray-700">Login</a>
          </div>
        </div>
      </body>
    </html>
    ''')

# Signup Page
@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        frequency = request.form.get('frequency')  # daily, weekly, monthly
        hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
        conn = sqlite3.connect('users.db')
        c = conn.cursor()
        try:
            c.execute("INSERT INTO users (email, password, frequency) VALUES (?, ?, ?)", (email, hashed_password, frequency))
            conn.commit()
            flash('Signup successful! Please log in.', 'success')
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash('Email already exists. Please log in.', 'danger')
        finally:
            conn.close()
    return render_template_string('''
    <!DOCTYPE html>
    <html lang="en">
      <head>
        <title>Signup</title>
        <script src="https://cdn.tailwindcss.com"></script>
      </head>
      <body class="bg-gray-100 flex justify-center items-center h-screen">
        <div class="bg-white p-8 rounded-lg shadow-md w-96">
          <h2 class="text-2xl font-bold text-gray-800 text-center">Create an Account</h2>
          <form method="POST" class="mt-4 space-y-4">
            <input type="email" name="email" placeholder="Your Email" required class="w-full px-4 py-2 border rounded-lg focus:ring focus:ring-blue-300">
            <input type="password" name="password" placeholder="Password" required class="w-full px-4 py-2 border rounded-lg focus:ring focus:ring-blue-300">
            <div>
              <label class="block mb-1 font-semibold">Email Frequency:</label>
              <select name="frequency" class="w-full px-4 py-2 border rounded-lg focus:ring focus:ring-blue-300">
                <option value="daily">Daily</option>
                <option value="weekly">Weekly</option>
                <option value="monthly">Monthly</option>
              </select>
            </div>
            <button type="submit" class="w-full px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700">Sign Up</button>
          </form>
          <p class="text-center mt-3 text-gray-500">
            <a href="/login" class="text-blue-600 hover:underline">Already have an account? Login</a>
          </p>
        </div>
      </body>
    </html>
    ''')

# Login Page
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        conn = sqlite3.connect('users.db')
        c = conn.cursor()
        c.execute("SELECT id, email, password, frequency, last_sent FROM users WHERE email = ?", (email,))
        user = c.fetchone()
        conn.close()
        if user and bcrypt.check_password_hash(user[2], password):
            login_user(User(user[0], user[1], user[3], user[4]))
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid credentials.', 'danger')
    return render_template_string('''
    <!DOCTYPE html>
    <html lang="en">
      <head>
        <title>Login</title>
        <script src="https://cdn.tailwindcss.com"></script>
      </head>
      <body class="bg-gray-100 flex justify-center items-center h-screen">
        <div class="bg-white p-8 rounded-lg shadow-md w-96">
          <h2 class="text-2xl font-bold text-gray-800 text-center">Login</h2>
          <form method="POST" class="mt-4 space-y-4">
            <input type="email" name="email" placeholder="Your Email" required class="w-full px-4 py-2 border rounded-lg focus:ring focus:ring-blue-300">
            <input type="password" name="password" placeholder="Password" required class="w-full px-4 py-2 border rounded-lg focus:ring focus:ring-blue-300">
            <button type="submit" class="w-full px-4 py-2 bg-gray-600 text-white rounded-lg hover:bg-gray-700">Login</button>
          </form>
          <p class="text-center mt-3 text-gray-500">
            <a href="/signup" class="text-blue-600 hover:underline">Don't have an account? Signup</a>
          </p>
        </div>
      </body>
    </html>
    ''')

# Dashboard Page
@app.route('/dashboard')
@login_required
def dashboard():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("SELECT file_path FROM clippings WHERE user_id = ?", (current_user.id,))
    files = c.fetchall()
    conn.close()
    file_links = "".join(f'<li class="mt-2"><a href="/view/{file[0]}" class="text-blue-600 underline">{file[0]}</a></li>' for file in files)
    return render_template_string(f'''
    <!DOCTYPE html>
    <html lang="en">
      <head>
        <title>Dashboard</title>
        <script src="https://cdn.tailwindcss.com"></script>
      </head>
      <body class="bg-gray-100 p-10">
        <div class="max-w-4xl mx-auto bg-white p-8 rounded-lg shadow-lg">
          <h2 class="text-3xl font-bold text-gray-800">Welcome, {current_user.email}!</h2>
          <div class="mt-6">
            <h3 class="text-xl font-semibold">Your Uploaded Clippings:</h3>
            <ul class="mt-2 list-disc list-inside">
              {file_links if file_links else '<li class="text-gray-500">No clippings uploaded.</li>'}
            </ul>
          </div>
          <div class="mt-6">
            <form action="/upload" method="post" enctype="multipart/form-data" class="flex items-center space-x-4">
              <input type="file" name="file" class="p-2 border rounded">
              <button type="submit" class="px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700">Upload Clippings</button>
            </form>
          </div>
          <div class="mt-6 flex flex-col sm:flex-row sm:items-center sm:space-x-4">
            <form action="/update_frequency" method="post" class="flex items-center space-x-2">
              <label class="font-semibold">Frequency:</label>
              <select name="frequency" class="px-2 py-1 border rounded">
                <option value="daily" {'selected' if current_user.frequency=='daily' else ''}>Daily</option>
                <option value="weekly" {'selected' if current_user.frequency=='weekly' else ''}>Weekly</option>
                <option value="monthly" {'selected' if current_user.frequency=='monthly' else ''}>Monthly</option>
              </select>
              <button type="submit" class="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700">Update</button>
            </form>
            <form action="/send-now" method="post" class="flex items-center space-x-2 mt-4 sm:mt-0">
              <label class="font-semibold"># of Clippings:</label>
              <input type="number" name="num_clippings" value="5" min="1" class="w-20 px-2 py-1 border rounded">
              <button type="submit" class="px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700">Send Now</button>
            </form>
          </div>
          <div class="mt-6">
            <a href="/logout" class="text-red-600 underline">Logout</a>
          </div>
        </div>
      </body>
    </html>
    ''')

# Update Email Frequency Route
@app.route('/update_frequency', methods=['POST'])
@login_required
def update_frequency():
    frequency = request.form.get('frequency')
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("UPDATE users SET frequency = ? WHERE id = ?", (frequency, current_user.id))
    conn.commit()
    conn.close()
    flash("Frequency updated successfully.", "success")
    return redirect(url_for('dashboard'))

# Upload Clippings Route
@app.route('/upload', methods=['POST'])
@login_required
def upload_file():
    file = request.files['file']
    if file:
        filename = secure_filename(file.filename)
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)
        conn = sqlite3.connect('users.db')
        c = conn.cursor()
        c.execute("INSERT INTO clippings (user_id, file_path) VALUES (?, ?)", (current_user.id, file_path))
        conn.commit()
        conn.close()
    return redirect(url_for('dashboard'))

# View Clippings Route
@app.route('/view/<path:filename>')
@login_required
def view_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# Send Clippings Now Route (Manual Trigger)
@app.route('/send-now', methods=['POST'])
@login_required
def send_now():
    try:
        num = int(request.form.get('num_clippings', 5))
    except ValueError:
        num = 5
    email_content = generate_email_content(current_user.id, num_clippings=num)
    if send_email_to_user(current_user, email_content):
        conn = sqlite3.connect('users.db')
        c = conn.cursor()
        c.execute("UPDATE users SET last_sent = ? WHERE id = ?", (datetime.datetime.now().isoformat(), current_user.id))
        conn.commit()
        conn.close()
        flash("Clippings sent to your email!", "success")
    else:
        flash("Failed to send email. Please try again later.", "danger")
    return redirect(url_for('dashboard'))

# Helper: Generate Email Content from Clippings (with limit)
def generate_email_content(user_id, num_clippings=5):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("SELECT file_path FROM clippings WHERE user_id = ?", (user_id,))
    files = c.fetchall()
    conn.close()
    content_parts = []
    for file_tuple in files:
        file_path = file_tuple[0]
        try:
            clippings = read_clippings_from_file(file_path)
            clippings = [clip for clip in clippings if clip.strip()]
            if clippings:
                selected = random.sample(clippings, min(num_clippings, len(clippings)))
                formatted = "".join([separate_clipping(clip) for clip in selected])
                content_parts.append(formatted)
        except Exception as e:
            content_parts.append(f"<p>Error reading {file_path}: {e}</p>")
    return "<br>".join(content_parts) if content_parts else "<p>No clippings uploaded.</p>"

# Helper: Read clippings from a file using separator
def read_clippings_from_file(file_path, encoding="utf-8"):
    with open(file_path, 'r', encoding=encoding) as f:
        return f.read().split("==========\n")

# Helper: Format a single clipping into HTML
def separate_clipping(clipping):
    lines = [line.strip() for line in clipping.strip().split("\n") if line.strip()]
    if len(lines) < 2:
        return ""
    book_details = lines[0]
    highlight_text = lines[-1]  # Use the last non-empty line as the highlight text
    return (
        f"<div style='margin-bottom:20px; padding-bottom:10px; border-bottom:1px solid #ddd;'>"
        f"<h3 style='margin:0; font-size:18px; color:#333;'>{book_details}</h3>"
        f"<p style='margin:5px 0 0; font-size:16px; font-style:italic; color:#555;'>&ldquo;{highlight_text}&rdquo;</p>"
        f"</div>"
    )

# Logout Route
@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))

# --------------------------
# Run the App
# --------------------------
if __name__ == "__main__":
    app.run(debug=True)
