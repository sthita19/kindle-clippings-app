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

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "your_secure_secret_key")
bcrypt = Bcrypt(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login_view"  # references the function name for login

UPLOAD_FOLDER = 'uploads'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
ALLOWED_EXTENSIONS = {'txt'}

SENDER_EMAIL = os.getenv("SENDER_EMAIL", "example@gmail.com")
SENDER_PASSWORD = os.getenv("SENDER_PASSWORD", "your_app_password")

# --------------------------
# Database Initialization
# --------------------------
def init_db():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    # Table for users
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            frequency TEXT NOT NULL DEFAULT 'daily',
            last_sent TEXT
        )
    ''')
    # Only one clippings row per user. Overwrite on new upload.
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
# Clippings / Email Helpers
# --------------------------
def read_clippings_from_file(file_path, encoding="utf-8"):
    with open(file_path, 'r', encoding=encoding) as f:
        return f.read().split("==========\n")

def separate_clipping(clipping):
    lines = [line.strip() for line in clipping.strip().split("\n") if line.strip()]
    if len(lines) < 2:
        return ""
    book_details = lines[0]
    highlight_text = lines[-1]
    return (
        f"<div style='margin-bottom:20px; padding-bottom:10px; border-bottom:1px solid #ddd;'>"
        f"<h3 style='margin:0; font-size:18px; color:#333;'>{book_details}</h3>"
        f"<p style='margin:5px 0 0; font-size:16px; font-style:italic; color:#555;'>&ldquo;{highlight_text}&rdquo;</p>"
        f"</div>"
    )

def generate_email_content(user_id, num_clippings=5):
    """
    Only one file per user. We'll fetch that single path, read it, pick total number_of_clippings from it.
    """
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("SELECT file_path FROM clippings WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()

    if not row:
        return "<p>No clippings uploaded.</p>"

    file_path = row[0]
    all_clips = []
    try:
        raw_clips = read_clippings_from_file(file_path)
        for clip in raw_clips:
            if clip.strip():
                all_clips.append(clip)
    except Exception as e:
        return f"<p>Error reading {file_path}: {e}</p>"

    if not all_clips:
        return "<p>No clippings uploaded.</p>"

    selected = random.sample(all_clips, min(num_clippings, len(all_clips)))
    formatted = []
    for clip in selected:
        formatted.append(separate_clipping(clip))
    return "<br>".join(formatted)

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
# APScheduler for auto-sending
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
        send_flag = False

        if user.frequency == 'daily':
            if user.last_sent:
                last = datetime.datetime.fromisoformat(user.last_sent)
                if last.date() < now.date():
                    send_flag = True
            else:
                send_flag = True
        elif user.frequency == 'weekly':
            if now.weekday() == 0:  # Monday
                if user.last_sent:
                    last = datetime.datetime.fromisoformat(user.last_sent)
                    if last.isocalendar()[1] < now.isocalendar()[1]:
                        send_flag = True
                else:
                    send_flag = True
        elif user.frequency == 'monthly':
            if now.day == 1:
                if user.last_sent:
                    last = datetime.datetime.fromisoformat(user.last_sent)
                    if last.month < now.month or last.year < now.year:
                        send_flag = True
                else:
                    send_flag = True

        if send_flag:
            email_content = generate_email_content(user.id)
            if send_email_to_user(user, email_content):
                conn2 = sqlite3.connect('users.db')
                c2 = conn2.cursor()
                c2.execute("UPDATE users SET last_sent = ? WHERE id = ?", (now.isoformat(), user.id))
                conn2.commit()
                conn2.close()

scheduler = BackgroundScheduler()
scheduler.add_job(func=scheduled_email_job, trigger="cron", hour=9)
scheduler.start()

# --------------------------
# 1) Home
# --------------------------
@app.route('/')
def home():
    template = """
    <!DOCTYPE html>
    <html lang="en">
      <head>
        <title>Kindle Clippings - Home</title>
        <script src="https://cdn.tailwindcss.com"></script>
      </head>
      <body class="bg-gradient-to-r from-[#3674B5] to-[#A1E3F9]">
        <!-- Navbar -->
        <nav class="bg-white shadow p-4">
          <div class="container mx-auto flex justify-between items-center">
            <div class="text-xl font-bold text-[#3674B5]">
              <a href="{{ url_for('home') }}">Kindle Clippings</a>
            </div>
            <div class="space-x-4">
              <a href="{{ url_for('home') }}" class="text-[#3674B5] hover:text-[#578FCA]">Home</a>
              {% if current_user.is_authenticated %}
                <a href="{{ url_for('dashboard') }}" class="text-[#3674B5] hover:text-[#578FCA]">Dashboard</a>
                <a href="{{ url_for('browse_page') }}" class="text-[#3674B5] hover:text-[#578FCA]">Browse</a>
                <a href="{{ url_for('logout') }}" class="text-[#3674B5] hover:text-[#578FCA]">Logout</a>
              {% else %}
                <a href="{{ url_for('login_view') }}" class="text-[#3674B5] hover:text-[#578FCA]">Login</a>
                <a href="{{ url_for('signup_view') }}" class="text-[#3674B5] hover:text-[#578FCA]">Sign Up</a>
              {% endif %}
            </div>
          </div>
        </nav>

        <div class="flex flex-col items-center justify-center h-screen">
          <div class="p-10 bg-[#D1F8EF] rounded-xl shadow-2xl text-center">
            <!-- Flash messages -->
            {% with messages = get_flashed_messages(with_categories=true) %}
              {% if messages %}
                <div class="mb-4">
                  {% for category, message in messages %}
                    <div class="px-4 py-2 rounded mb-2 
                      {% if category == 'success' %}bg-green-200 text-green-800
                      {% elif category == 'danger' %}bg-red-200 text-red-800
                      {% else %}bg-blue-200 text-blue-800{% endif %}">
                      {{ message }}
                    </div>
                  {% endfor %}
                </div>
              {% endif %}
            {% endwith %}
            <h1 class="text-5xl font-bold text-gray-800">📖 Kindle Clippings</h1>
            <p class="text-gray-700 mt-4">Upload your Kindle highlights and receive them by email at your chosen frequency.</p>
            <div class="mt-8 space-x-6">
              <a href="{{ url_for('signup_view') }}" class="px-8 py-3 bg-[#578FCA] text-white rounded-full shadow-lg hover:bg-[#3674B5]">Sign Up</a>
              <a href="{{ url_for('login_view') }}" class="px-8 py-3 bg-gray-800 text-white rounded-full shadow-lg hover:bg-gray-900">Login</a>
            </div>
          </div>
        </div>
      </body>
    </html>
    """
    return render_template_string(template)

# --------------------------
# 2) Signup
# --------------------------
@app.route('/signup', methods=['GET', 'POST'])
def signup_view():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        frequency = request.form.get('frequency')
        hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')

        conn = sqlite3.connect('users.db')
        c = conn.cursor()
        try:
            c.execute("INSERT INTO users (email, password, frequency) VALUES (?, ?, ?)",
                      (email, hashed_password, frequency))
            conn.commit()
            flash('Signup successful! Please log in.', 'success')
            return redirect(url_for('login_view'))
        except sqlite3.IntegrityError:
            flash('Email already exists. Please log in.', 'danger')
        finally:
            conn.close()

    template = """
    <!DOCTYPE html>
    <html lang="en">
      <head>
        <title>Kindle Clippings - Signup</title>
        <script src="https://cdn.tailwindcss.com"></script>
      </head>
      <body class="bg-gradient-to-r from-[#A1E3F9] to-[#D1F8EF]">
        <!-- Navbar -->
        <nav class="bg-white shadow p-4">
          <div class="container mx-auto flex justify-between items-center">
            <div class="text-xl font-bold text-[#3674B5]">
              <a href="{{ url_for('home') }}">Kindle Clippings</a>
            </div>
            <div class="space-x-4">
              <a href="{{ url_for('home') }}" class="text-[#3674B5] hover:text-[#578FCA]">Home</a>
              {% if current_user.is_authenticated %}
                <a href="{{ url_for('dashboard') }}" class="text-[#3674B5] hover:text-[#578FCA]">Dashboard</a>
                <a href="{{ url_for('browse_page') }}" class="text-[#3674B5] hover:text-[#578FCA]">Browse</a>
                <a href="{{ url_for('logout') }}" class="text-[#3674B5] hover:text-[#578FCA]">Logout</a>
              {% else %}
                <a href="{{ url_for('login_view') }}" class="text-[#3674B5] hover:text-[#578FCA]">Login</a>
                <a href="{{ url_for('signup_view') }}" class="text-[#3674B5] hover:text-[#578FCA]">Sign Up</a>
              {% endif %}
            </div>
          </div>
        </nav>

        <div class="flex justify-center items-center h-screen">
          <div class="bg-white p-10 rounded-xl shadow-2xl w-96">
            <!-- Flash messages -->
            {% with messages = get_flashed_messages(with_categories=true) %}
              {% if messages %}
                <div class="mb-4">
                  {% for category, message in messages %}
                    <div class="px-4 py-2 rounded mb-2 
                      {% if category == 'success' %}bg-green-200 text-green-800
                      {% elif category == 'danger' %}bg-red-200 text-red-800
                      {% else %}bg-blue-200 text-blue-800{% endif %}">
                      {{ message }}
                    </div>
                  {% endfor %}
                </div>
              {% endif %}
            {% endwith %}
            <h2 class="text-3xl font-bold text-gray-800 text-center">Create an Account</h2>
            <form method="POST" class="mt-6 space-y-5">
              <input type="email" name="email" placeholder="Your Email" required class="w-full px-4 py-2 border rounded-lg focus:ring focus:ring-[#3674B5]">
              <input type="password" name="password" placeholder="Password" required class="w-full px-4 py-2 border rounded-lg focus:ring focus:ring-[#3674B5]">
              <div>
                <label class="block mb-2 font-semibold text-gray-700">Email Frequency:</label>
                <select name="frequency" class="w-full px-4 py-2 border rounded-lg focus:ring focus:ring-[#3674B5]">
                  <option value="daily">Daily</option>
                  <option value="weekly">Weekly</option>
                  <option value="monthly">Monthly</option>
                </select>
              </div>
              <button type="submit" class="w-full px-4 py-3 bg-[#578FCA] text-white rounded-full hover:bg-[#3674B5] shadow-lg">Sign Up</button>
            </form>
            <p class="mt-4 text-center text-gray-700">
              <a href="{{ url_for('login_view') }}" class="text-[#3674B5] hover:underline">Already have an account? Login</a>
            </p>
          </div>
        </div>
      </body>
    </html>
    """
    return render_template_string(template)

# --------------------------
# 3) Login Page
# --------------------------
@app.route('/login', methods=['GET', 'POST'])
def login_view():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        conn = sqlite3.connect('users.db')
        c = conn.cursor()
        c.execute("SELECT id, email, password, frequency, last_sent FROM users WHERE email = ?", (email,))
        user = c.fetchone()
        conn.close()
        if user is None:
            flash('Account does not exist. Please sign up.', 'danger')
        elif not bcrypt.check_password_hash(user[2], password):
            flash('Wrong password. Please try again.', 'danger')
        else:
            login_user(User(user[0], user[1], user[3], user[4]))
            return redirect(url_for('dashboard'))

    template = """
    <!DOCTYPE html>
    <html lang="en">
      <head>
        <title>Kindle Clippings - Login</title>
        <script src="https://cdn.tailwindcss.com"></script>
      </head>
      <body class="bg-gradient-to-r from-[#578FCA] to-[#3674B5]">
        <!-- Navbar -->
        <nav class="bg-white shadow p-4">
          <div class="container mx-auto flex justify-between items-center">
            <div class="text-xl font-bold text-[#3674B5]">
              <a href="{{ url_for('home') }}">Kindle Clippings</a>
            </div>
            <div class="space-x-4">
              <a href="{{ url_for('home') }}" class="text-[#3674B5] hover:text-[#578FCA]">Home</a>
              {% if current_user.is_authenticated %}
                <a href="{{ url_for('dashboard') }}" class="text-[#3674B5] hover:text-[#578FCA]">Dashboard</a>
                <a href="{{ url_for('browse_page') }}" class="text-[#3674B5] hover:text-[#578FCA]">Browse</a>
                <a href="{{ url_for('logout') }}" class="text-[#3674B5] hover:text-[#578FCA]">Logout</a>
              {% else %}
                <a href="{{ url_for('login_view') }}" class="text-[#3674B5] hover:text-[#578FCA]">Login</a>
                <a href="{{ url_for('signup_view') }}" class="text-[#3674B5] hover:text-[#578FCA]">Sign Up</a>
              {% endif %}
            </div>
          </div>
        </nav>

        <div class="flex justify-center items-center h-screen">
          <div class="bg-white p-10 rounded-xl shadow-2xl w-96">
            <!-- Flash messages -->
            {% with messages = get_flashed_messages(with_categories=true) %}
              {% if messages %}
                <div class="mb-4">
                  {% for category, message in messages %}
                    <div class="px-4 py-2 rounded mb-2 
                      {% if category == 'success' %}bg-green-200 text-green-800
                      {% elif category == 'danger' %}bg-red-200 text-red-800
                      {% else %}bg-blue-200 text-blue-800{% endif %}">
                      {{ message }}
                    </div>
                  {% endfor %}
                </div>
              {% endif %}
            {% endwith %}
            <h2 class="text-3xl font-bold text-gray-800 text-center">Login</h2>
            <form method="POST" class="mt-6 space-y-5">
              <input type="email" name="email" placeholder="Your Email" required class="w-full px-4 py-2 border rounded-lg focus:ring focus:ring-[#3674B5]">
              <input type="password" name="password" placeholder="Password" required class="w-full px-4 py-2 border rounded-lg focus:ring focus:ring-[#3674B5]">
              <button type="submit" class="w-full px-4 py-3 bg-gray-800 text-white rounded-full hover:bg-gray-900 shadow-lg">Login</button>
            </form>
            <p class="mt-4 text-center text-gray-700">
              <a href="{{ url_for('signup_view') }}" class="text-[#3674B5] hover:underline">Don't have an account? Signup</a>
            </p>
          </div>
        </div>
      </body>
    </html>
    """
    return render_template_string(template)

# Overwrite `login_manager.login_view` = "login_view"
# If you want a second name, that's fine, but this is consistent.

# --------------------------
# 4) Dashboard
# --------------------------
@app.route('/dashboard')
@login_required
def dashboard():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("SELECT file_path FROM clippings WHERE user_id = ?", (current_user.id,))
    row = c.fetchone()
    conn.close()

    if row:
        current_file = os.path.basename(row[0])
        file_display = f"<p class='mt-2 text-[#3674B5] underline'>{current_file}</p>"
    else:
        file_display = "<p class='mt-2 text-gray-500'>No clippings file uploaded yet.</p>"

    template = f"""
    <!DOCTYPE html>
    <html lang="en">
      <head>
        <title>Kindle Clippings - Dashboard</title>
        <script src="https://cdn.tailwindcss.com"></script>
      </head>
      <body class="bg-[#A1E3F9]">
        <!-- Navbar -->
        <nav class="bg-white shadow p-4">
          <div class="container mx-auto flex justify-between items-center">
            <div class="text-xl font-bold text-[#3674B5]">
              <a href="{{{{ url_for('home') }}}}">Kindle Clippings</a>
            </div>
            <div class="space-x-4">
              <a href="{{{{ url_for('home') }}}}" class="text-[#3674B5] hover:text-[#578FCA]">Home</a>
              {{% if current_user.is_authenticated %}}
                <a href="{{{{ url_for('dashboard') }}}}" class="text-[#3674B5] hover:text-[#578FCA]">Dashboard</a>
                <a href="{{{{ url_for('browse_page') }}}}" class="text-[#3674B5] hover:text-[#578FCA]">Browse</a>
                <a href="{{{{ url_for('logout') }}}}" class="text-[#3674B5] hover:text-[#578FCA]">Logout</a>
              {{% else %}}
                <a href="{{{{ url_for('login_view') }}}}" class="text-[#3674B5] hover:text-[#578FCA]">Login</a>
                <a href="{{{{ url_for('signup_view') }}}}" class="text-[#3674B5] hover:text-[#578FCA]">Sign Up</a>
              {{% endif %}}
            </div>
          </div>
        </nav>

        <div class="p-10">
          <div class="max-w-5xl mx-auto bg-white p-8 rounded-xl shadow-2xl">
            <!-- Flash messages -->
            {{% with messages = get_flashed_messages(with_categories=true) %}}
              {{% if messages %}}
                <div class="mb-4">
                  {{% for category, message in messages %}}
                    <div class="px-4 py-2 rounded mb-2 
                      {{% if category == 'success' %}}bg-green-200 text-green-800
                      {{% elif category == 'danger' %}}bg-red-200 text-red-800
                      {{% else %}}bg-blue-200 text-blue-800{{% endif %}}">
                      {{{{ message }}}}
                    </div>
                  {{% endfor %}}
                </div>
              {{% endif %}}
            {{% endwith %}}

            <h2 class="text-4xl font-bold text-gray-800">Welcome, {{{{ current_user.email }}}}!</h2>
            <div class="mt-4 text-gray-800">Your current clippings file:</div>
            {file_display}
            <div class="mt-8">
              <a href="{{{{ url_for('browse_page') }}}}" class="px-6 py-3 bg-[#578FCA] text-white rounded-full shadow-lg hover:bg-[#3674B5]">Browse Clippings</a>
            </div>
            <div class="mt-8">
              <form action="{{{{ url_for('upload_file') }}}}" method="post" enctype="multipart/form-data" class="flex flex-col sm:flex-row items-center sm:space-x-4">
                <input type="file" name="file" class="p-2 border rounded mb-4 sm:mb-0">
                <button type="submit" class="px-6 py-3 bg-[#578FCA] text-white rounded-full hover:bg-[#3674B5] shadow-lg">
                  Overwrite Current File
                </button>
              </form>
            </div>
            <div class="mt-8 flex flex-col sm:flex-row sm:items-center sm:space-x-4">
              <form action="{{{{ url_for('update_frequency') }}}}" method="post" class="flex items-center space-x-2">
                <label class="font-semibold text-gray-800">Frequency:</label>
                <select name="frequency" class="px-3 py-2 border rounded-lg focus:ring focus:ring-[#3674B5]">
                  <option value="daily" {{% if current_user.frequency == 'daily' %}}selected{{% endif %}}>Daily</option>
                  <option value="weekly" {{% if current_user.frequency == 'weekly' %}}selected{{% endif %}}>Weekly</option>
                  <option value="monthly" {{% if current_user.frequency == 'monthly' %}}selected{{% endif %}}>Monthly</option>
                </select>
                <button type="submit" class="px-5 py-2 bg-[#578FCA] text-white rounded-full hover:bg-[#3674B5] shadow-lg">
                  Confirm Frequency
                </button>
              </form>
              <form action="{{{{ url_for('send_now') }}}}" method="post" class="flex items-center space-x-2 mt-4 sm:mt-0">
                <label class="font-semibold text-gray-800"># of Clippings:</label>
                <input type="number" name="num_clippings" value="5" min="1" class="w-20 px-3 py-2 border rounded-lg">
                <button type="submit" class="px-5 py-2 bg-indigo-600 text-white rounded-full hover:bg-indigo-700 shadow-lg">
                  Send Now
                </button>
              </form>
            </div>
          </div>
        </div>
      </body>
    </html>
    """

    return render_template_string(template)

# --------------------------
# 5) Browse Page
# --------------------------
@app.route('/browse', methods=['GET'])
@login_required
def browse_page():
    query = request.args.get('query', '').strip().lower()
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("SELECT file_path FROM clippings WHERE user_id = ?", (current_user.id,))
    row = c.fetchone()
    conn.close()

    if not row:
        # No file
        grouped_html = "<p>No file uploaded yet.</p>"
    else:
        file_path = row[0]
        books = {}
        try:
            raw_clips = read_clippings_from_file(file_path)
            for clip in raw_clips:
                lines = [line.strip() for line in clip.strip().split("\n") if line.strip()]
                if len(lines) < 2:
                    continue
                book_title = lines[0]
                if query and query not in book_title.lower():
                    continue
                books.setdefault(book_title, []).append(separate_clipping(clip))
        except Exception as e:
            grouped_html = f"<p>Error reading {file_path}: {e}</p>"
            books = {}

        if books:
            grouped_html = ""
            for book, clips in books.items():
                grouped_html += f"<h3 class='text-2xl font-bold text-gray-800 mt-6'>{book}</h3>"
                grouped_html += "".join(clips)
            if not grouped_html:
                grouped_html = "<p class='text-gray-500'>No clippings found for this search.</p>"
        else:
            if 'grouped_html' not in locals():
                grouped_html = "<p>No clippings found in file.</p>"

    template = """
    <!DOCTYPE html>
    <html lang="en">
      <head>
        <title>Kindle Clippings - Browse</title>
        <script src="https://cdn.tailwindcss.com"></script>
      </head>
      <body class="bg-gradient-to-r from-[#A1E3F9] to-[#D1F8EF]">
        <!-- Navbar -->
        <nav class="bg-white shadow p-4">
          <div class="container mx-auto flex justify-between items-center">
            <div class="text-xl font-bold text-[#3674B5]">
              <a href="{{ url_for('home') }}">Kindle Clippings</a>
            </div>
            <div class="space-x-4">
              <a href="{{ url_for('home') }}" class="text-[#3674B5] hover:text-[#578FCA]">Home</a>
              {% if current_user.is_authenticated %}
                <a href="{{ url_for('dashboard') }}" class="text-[#3674B5] hover:text-[#578FCA]">Dashboard</a>
                <a href="{{ url_for('browse_page') }}" class="text-[#3674B5] hover:text-[#578FCA]">Browse</a>
                <a href="{{ url_for('logout') }}" class="text-[#3674B5] hover:text-[#578FCA]">Logout</a>
              {% else %}
                <a href="{{ url_for('login_view') }}" class="text-[#3674B5] hover:text-[#578FCA]">Login</a>
                <a href="{{ url_for('signup_view') }}" class="text-[#3674B5] hover:text-[#578FCA]">Sign Up</a>
              {% endif %}
            </div>
          </div>
        </nav>

        <div class="p-10">
          <div class="max-w-5xl mx-auto bg-white p-8 rounded-xl shadow-2xl">
            <!-- Flash messages -->
            {% with messages = get_flashed_messages(with_categories=true) %}
              {% if messages %}
                <div class="mb-4">
                  {% for category, message in messages %}
                    <div class="px-4 py-2 rounded mb-2 
                      {% if category == 'success' %}bg-green-200 text-green-800
                      {% elif category == 'danger' %}bg-red-200 text-red-800
                      {% else %}bg-blue-200 text-blue-800{% endif %}">
                      {{ message }}
                    </div>
                  {% endfor %}
                </div>
              {% endif %}
            {% endwith %}

            <h2 class="text-4xl font-bold text-gray-800">Browse Your Clippings</h2>
            <form method="GET" action="{{ url_for('browse_page') }}" class="mt-6 flex items-center space-x-4">
              <input type="text" name="query" placeholder="Search by book title" value="{{ request.args.get('query', '') }}" class="w-full px-4 py-2 border rounded-lg focus:ring focus:ring-[#3674B5]">
              <button type="submit" class="px-6 py-2 bg-blue-600 text-white rounded-full hover:bg-blue-700 shadow-lg">Search</button>
            </form>
            <div class="mt-8">
              {{ grouped_html|safe }}
            </div>
            <div class="mt-8">
              <a href="{{ url_for('dashboard') }}" class="text-[#3674B5] underline">Back to Dashboard</a>
            </div>
          </div>
        </div>
      </body>
    </html>
    """
    return render_template_string(template, grouped_html=grouped_html)

# --------------------------
# 6) Update Frequency, Overwrite Upload, Send, Logout
# --------------------------
@app.route('/update_frequency', methods=['POST'])
@login_required
def update_frequency():
    new_freq = request.form.get('frequency', 'daily')
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("UPDATE users SET frequency=? WHERE id=?", (new_freq, current_user.id))
    conn.commit()
    conn.close()
    flash("Frequency updated successfully.", "success")
    return redirect(url_for('dashboard'))

@app.route('/upload', methods=['POST'])
@login_required
def upload_file():
    file = request.files.get('file')
    if file:
        filename = secure_filename(file.filename)
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)

        # Overwrite old row for the user
        conn = sqlite3.connect('users.db')
        c = conn.cursor()
        c.execute("DELETE FROM clippings WHERE user_id=?", (current_user.id,))
        c.execute("INSERT INTO clippings (user_id, file_path) VALUES (?, ?)", (current_user.id, file_path))
        conn.commit()
        conn.close()
        flash("File uploaded and overwritten successfully!", "success")

    return redirect(url_for('dashboard'))

@app.route('/view/<path:filename>')
@login_required
def view_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/send-now', methods=['POST'])
@login_required
def send_now():
    try:
        num = int(request.form.get('num_clippings', 5))
    except ValueError:
        num = 5
    email_body = generate_email_content(current_user.id, num_clippings=num)
    if send_email_to_user(current_user, email_body):
        conn = sqlite3.connect('users.db')
        c = conn.cursor()
        c.execute("UPDATE users SET last_sent=? WHERE id=?", (datetime.datetime.now().isoformat(), current_user.id))
        conn.commit()
        conn.close()
        flash("Clippings sent to your email!", "success")
    else:
        flash("Failed to send email. Please try again later.", "danger")
    return redirect(url_for('dashboard'))

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login_view'))

if __name__ == "__main__":
    app.run(debug=True)
