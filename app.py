import os
import sqlite3
import datetime
import random
import smtplib
import boto3
import re
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_bcrypt import Bcrypt
from werkzeug.utils import secure_filename
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "your_secret_key")
bcrypt = Bcrypt(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

# S3 configuration
AWS_BUCKET_NAME = os.getenv("AWS_BUCKET_NAME", "my-kindle-clippings-app")
AWS_REGION = os.getenv("AWS_REGION", "ap-south-1")
s3 = boto3.client('s3', region_name=AWS_REGION)

# (Optional) We'll still define an uploads folder for local development fallback
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

SENDER_EMAIL = os.getenv("SENDER_EMAIL", "example@gmail.com")
SENDER_PASSWORD = os.getenv("SENDER_PASSWORD", "app_password")

##########################
# Database Initialization
##########################
def init_db():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            frequency TEXT NOT NULL DEFAULT 'daily',
            last_sent TEXT,
            send_time TEXT NOT NULL DEFAULT '09:00',
            notifications_paused INTEGER NOT NULL DEFAULT 0
        )
    ''')
    # Instead of storing a local file path, we store the S3 key
    c.execute('''
        CREATE TABLE IF NOT EXISTS clippings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            s3_key TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    ''')
    conn.commit()
    conn.close()

init_db()

##########################
# Flask-Login: User Model
##########################
class User(UserMixin):
    def __init__(self, user_id, email, frequency='daily', last_sent=None, send_time='09:00', notifications_paused=0):
        self.id = user_id
        self.email = email
        self.frequency = frequency
        self.last_sent = last_sent
        self.send_time = send_time
        self.notifications_paused = int(notifications_paused)

@login_manager.user_loader
def load_user(user_id):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("SELECT id, email, frequency, last_sent, send_time, notifications_paused FROM users WHERE id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return User(*row)
    return None

##########################
# AWS S3 File Helpers
##########################
def upload_file_to_s3(file_obj, filename, user_id):
    """
    Uploads file_obj to S3 under a key using the user_id and filename.
    Returns the S3 key.
    """
    # Create a unique S3 key, for example: "user_{id}/filename"
    s3_key = f"user_{user_id}/{secure_filename(filename)}"
    s3.upload_fileobj(file_obj, AWS_BUCKET_NAME, s3_key)
    return s3_key


def read_clippings_from_s3(s3_key):
    response = s3.get_object(Bucket=AWS_BUCKET_NAME, Key=s3_key)
    content = response['Body'].read().decode('utf-8-sig')
    # Use a regex to split on lines that consist solely of '=' (with optional whitespace)
    clips = re.split(r'\n\s*=+\s*\n', content)
    return clips

##########################
# Clippings & Email Helpers
##########################
def separate_clipping(clipping):
    lines = [line.strip() for line in clipping.strip().split('\n') if line.strip()]
    if len(lines) < 2:
        return ""
    book_title = lines[0]
    highlight_text = lines[-1]
    return (
        "<div style='margin-bottom:20px; border-bottom:1px solid #ccc; padding-bottom:10px;'>"
        f"<h3 style='margin:0; font-size:18px; color:#333;'>{book_title}</h3>"
        f"<p style='margin:5px 0 0; font-size:16px; font-style:italic;'>&ldquo;{highlight_text}&rdquo;</p>"
        "</div>"
    )

def generate_email_content(user_id, num_clippings=5):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("SELECT s3_key FROM clippings WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return "<p>No clippings uploaded.</p>"
    s3_key = row[0]
    try:
        raw_clips = read_clippings_from_s3(s3_key)
        clips = [clip for clip in raw_clips if clip.strip()]
    except Exception as e:
        return f"<p>Error reading file from S3: {e}</p>"
    if not clips:
        return "<p>No clippings uploaded.</p>"
    selected = random.sample(clips, min(num_clippings, len(clips)))
    return "".join(separate_clipping(clip) for clip in selected)

def send_email_to_user(user, email_html):
    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        msg = MIMEMultipart()
        msg['From'] = SENDER_EMAIL
        msg['To'] = user.email
        msg['Subject'] = "Your Kindle Clippings"
        msg.attach(MIMEText(email_html, 'html'))
        server.sendmail(SENDER_EMAIL, user.email, msg.as_string())
        server.quit()
        print(f"Email sent to {user.email}")
        return True
    except Exception as ex:
        print("Error sending email:", ex)
        return False

##########################
# Analytics Helper
##########################
def get_analytics(user_id):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("SELECT s3_key FROM clippings WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return 0, "N/A"
    s3_key = row[0]
    try:
        raw_clips = read_clippings_from_s3(s3_key)
        valid_clips = [clip for clip in raw_clips if clip.strip()]
        total = len(valid_clips)
        counts = {}
        for clip in valid_clips:
            lines = [ln.strip() for ln in clip.split('\n') if ln.strip()]
            if lines:
                book_title = lines[0]
                counts[book_title] = counts.get(book_title, 0) + 1
        most_highlighted = max(counts, key=counts.get) if counts else "N/A"
        return total, most_highlighted
    except Exception as e:
        return 0, f"Error: {e}"

def get_book_distribution(user_id):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("SELECT s3_key FROM clippings WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    distribution = {}
    if not row:
        return distribution
    s3_key = row[0]
    try:
        raw_data = read_clippings_from_s3(s3_key)
        for clip in raw_data:
            lines = [ln.strip() for ln in clip.split('\n') if ln.strip()]
            if len(lines) >= 2:
                book = lines[0]
                distribution[book] = distribution.get(book, 0) + 1
    except:
        pass
    return distribution

##########################
# APScheduler for Advanced Scheduling
##########################
def scheduled_email_job():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("SELECT id, email, frequency, last_sent, send_time, notifications_paused FROM users")
    rows = c.fetchall()
    conn.close()
    now = datetime.datetime.now()
    current_time = now.strftime("%H:%M")
    for row in rows:
        user_obj = User(*row)
        if user_obj.notifications_paused == 1:
            continue
        if current_time != user_obj.send_time:
            continue
        send_flag = False
        if user_obj.frequency == 'daily':
            if user_obj.last_sent:
                last_dt = datetime.datetime.fromisoformat(user_obj.last_sent)
                if last_dt.date() < now.date():
                    send_flag = True
            else:
                send_flag = True
        elif user_obj.frequency == 'weekly':
            if now.weekday() == 0:
                if user_obj.last_sent:
                    last_dt = datetime.datetime.fromisoformat(user_obj.last_sent)
                    if last_dt.isocalendar()[1] < now.isocalendar()[1]:
                        send_flag = True
                else:
                    send_flag = True
        elif user_obj.frequency == 'monthly':
            if now.day == 1:
                if user_obj.last_sent:
                    last_dt = datetime.datetime.fromisoformat(user_obj.last_sent)
                    if last_dt.month < now.month or last_dt.year < now.year:
                        send_flag = True
                else:
                    send_flag = True
        if send_flag:
            content = generate_email_content(user_obj.id)
            if send_email_to_user(user_obj, content):
                conn2 = sqlite3.connect('users.db')
                c2 = conn2.cursor()
                c2.execute("UPDATE users SET last_sent=? WHERE id=?", (now.isoformat(), user_obj.id))
                conn2.commit()
                conn2.close()

scheduler = BackgroundScheduler()
scheduler.add_job(func=scheduled_email_job, trigger="cron", minute="*")
scheduler.start()

##########################
# Routes
##########################

@app.route("/")
def home():
    return render_template("home.html")

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]
        freq = request.form.get("frequency", "daily")
        conn = sqlite3.connect("users.db")
        c = conn.cursor()
        hashed_pw = bcrypt.generate_password_hash(password).decode("utf-8")
        try:
            c.execute("INSERT INTO users (email, password, frequency) VALUES (?, ?, ?)", (email, hashed_pw, freq))
            conn.commit()
            flash("Signup successful! Please log in.", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("Email already exists. Please log in.", "danger")
        finally:
            conn.close()
    return render_template("signup.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]
        conn = sqlite3.connect("users.db")
        c = conn.cursor()
        c.execute("""SELECT id, email, password, frequency, last_sent, send_time, notifications_paused
                     FROM users WHERE email=?""", (email,))
        row = c.fetchone()
        conn.close()
        if row is None:
            flash("Account does not exist. Please sign up.", "danger")
        else:
            user_id, user_email, user_pw, freq, last_sent, stime, paused = row
            if not bcrypt.check_password_hash(user_pw, password):
                flash("Wrong password. Please try again.", "danger")
            else:
                user_obj = User(user_id, user_email, freq, last_sent, stime, paused)
                login_user(user_obj)
                return redirect(url_for("dashboard"))
    return render_template("login.html")

@app.route("/dashboard")
@login_required
def dashboard():
    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    c.execute("SELECT s3_key FROM clippings WHERE user_id=?", (current_user.id,))
    row = c.fetchone()
    conn.close()
    if row:
        file_name = row[0].split("/")[-1]
        total, most_book = get_analytics(current_user.id)
        file_display = file_name
    else:
        file_display = "No clippings file uploaded yet."
        total, most_book = 0, "N/A"
    distribution = get_book_distribution(current_user.id)
    labels = list(distribution.keys())
    counts = list(distribution.values())
    return render_template("dashboard.html",
                           file_display=file_display,
                           total=total,
                           most_highlighted=most_book,
                           chart_labels=labels,
                           chart_counts=counts)

@app.route("/browse")
@login_required
def browse():
    query = request.args.get("query", "").strip().lower()
    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    c.execute("SELECT s3_key FROM clippings WHERE user_id=?", (current_user.id,))
    row = c.fetchone()
    conn.close()
    grouped_html = ""
    if row:
        key = row[0]
        books = {}
        try:
            raw_clips = read_clippings_from_s3(key)
            for clip in raw_clips:
                lines = [ln.strip() for ln in clip.split('\n') if ln.strip()]
                if len(lines) < 2:
                    continue
                book_title = lines[0]
                if query and query not in book_title.lower():
                    continue
                books.setdefault(book_title, []).append(separate_clipping(clip))
        except Exception as e:
            grouped_html = f"<p>Error reading file: {e}</p>"
            books = {}
        if books:
            grouped_html = ""
            for bk, parts in books.items():
                grouped_html += f"<h3 class='text-2xl font-bold mt-6'>{bk}</h3>"
                grouped_html += "".join(parts)
            if not grouped_html:
                grouped_html = "<p>No matching clippings found.</p>"
        else:
            grouped_html = "<p>No clippings found in this file.</p>"
    else:
        grouped_html = "<p>No file uploaded yet.</p>"
    return render_template("browse.html", grouped_html=grouped_html)

@app.route("/update_scheduling", methods=["POST"])
@login_required
def update_scheduling():
    stime = request.form.get("send_time", "09:00")
    paused = 1 if request.form.get("notifications_paused") == "on" else 0
    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    c.execute("UPDATE users SET send_time=?, notifications_paused=? WHERE id=?", (stime, paused, current_user.id))
    conn.commit()
    conn.close()
    flash("Scheduling options updated successfully.", "success")
    return redirect(url_for("dashboard"))

@app.route("/update_frequency", methods=["POST"])
@login_required
def update_frequency():
    freq = request.form.get("frequency", "daily")
    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    c.execute("UPDATE users SET frequency=? WHERE id=?", (freq, current_user.id))
    conn.commit()
    conn.close()
    flash("Frequency updated successfully.", "success")
    return redirect(url_for("dashboard"))

@app.route("/upload", methods=["POST"])
@login_required
def upload_file():
    f = request.files.get("file")
    if f:
        fname = secure_filename(f.filename)
        # Upload to S3 instead of local storage
        s3_key = upload_file_to_s3(f, fname, current_user.id)
        conn = sqlite3.connect("users.db")
        c = conn.cursor()
        c.execute("DELETE FROM clippings WHERE user_id=?", (current_user.id,))
        c.execute("INSERT INTO clippings (user_id, s3_key) VALUES (?, ?)", (current_user.id, s3_key))
        conn.commit()
        conn.close()
        flash("File uploaded & stored successfully!", "success")
    return redirect(url_for("dashboard"))

@app.route("/send-now", methods=["POST"])
@login_required
def send_now():
    try:
        n = int(request.form.get("num_clippings", 5))
    except ValueError:
        n = 5
    email_html = generate_email_content(current_user.id, num_clippings=n)
    if send_email_to_user(current_user, email_html):
        conn = sqlite3.connect("users.db")
        c = conn.cursor()
        c.execute("UPDATE users SET last_sent=? WHERE id=?", (datetime.datetime.now().isoformat(), current_user.id))
        conn.commit()
        conn.close()
        flash("Clippings sent to your email!", "success")
    else:
        flash("Failed to send email. Please try again later.", "danger")
    return redirect(url_for("dashboard"))

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))

@app.route("/view/<path:filename>")
@login_required
def view_file(filename):
    # For local testing, you can still view files if needed (not S3)
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

if __name__ == "__main__":
    app.run(debug=True)
