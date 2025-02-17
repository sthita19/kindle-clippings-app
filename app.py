import os
import sqlite3
import random
import smtplib
import boto3
import re
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_bcrypt import Bcrypt
from werkzeug.utils import secure_filename
from apscheduler.schedulers.background import BackgroundScheduler
import pytz

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

UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

SENDER_EMAIL = os.getenv("SENDER_EMAIL", "example@gmail.com")
SENDER_PASSWORD = os.getenv("SENDER_PASSWORD", "app_password")

# Indian Standard Time (IST)
IST = pytz.timezone('Asia/Kolkata')
@app.template_filter('friendly_time')
def friendly_time_filter(iso_str):
    try:
        dt = datetime.fromisoformat(iso_str)
        # Convert UTC -> IST if dt doesn't have tzinfo or is in UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=pytz.utc)
        dt_ist = dt.astimezone(IST)
        return dt_ist.strftime("%b %d, %Y %I:%M %p")  # e.g. "Apr 15, 2025 05:12 PM"
    except Exception:
        return iso_str  # fallback if parsing fails
##########################
# Database Initialization
##########################
def init_db():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()

    # Create users table with columns for scheduling & num_clippings
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            frequency TEXT NOT NULL DEFAULT 'daily',
            last_sent TEXT,
            send_time TEXT NOT NULL DEFAULT '09:00',
            notifications_paused INTEGER NOT NULL DEFAULT 0,
            num_clippings INTEGER NOT NULL DEFAULT 5
        )
    ''')

    # Single S3 key per user
    c.execute('''
        CREATE TABLE IF NOT EXISTS clippings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            s3_key TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    ''')

    # Email history table
    c.execute('''
        CREATE TABLE IF NOT EXISTS email_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            sent_at TEXT NOT NULL,
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
    def __init__(self,
                 user_id,
                 email,
                 frequency='daily',
                 last_sent=None,
                 send_time='09:00',
                 notifications_paused=0,
                 num_clippings=5):
        self.id = user_id
        self.email = email
        self.frequency = frequency
        self.last_sent = last_sent
        self.send_time = send_time
        self.notifications_paused = int(notifications_paused)
        self.num_clippings = int(num_clippings)

@login_manager.user_loader
def load_user(user_id):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    # retrieve num_clippings from DB
    c.execute("""
        SELECT id, email, frequency, last_sent, send_time, notifications_paused, num_clippings
        FROM users
        WHERE id=?
    """, (user_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return User(*row)
    return None

##########################
# AWS S3 File Helpers
##########################
def upload_file_to_s3(file_obj, filename, user_id):
    s3_key = f"user_{user_id}/{secure_filename(filename)}"
    s3.upload_fileobj(file_obj, AWS_BUCKET_NAME, s3_key)
    return s3_key

def read_clippings_from_s3(s3_key):
    response = s3.get_object(Bucket=AWS_BUCKET_NAME, Key=s3_key)
    content = response['Body'].read().decode('utf-8-sig')
    # Use regex to split on lines that consist solely of "="
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
        "<div class='mb-5 border-b border-gray-300 pb-2'>"
        f"<h3 class='text-lg font-semibold text-gray-800 dark:text-gray-200'>{book_title}</h3>"
        f"<p class='mt-1 text-base italic text-gray-600 dark:text-gray-400'>&ldquo;{highlight_text}&rdquo;</p>"
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
# Analytics / Email History
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

def get_email_history(user_id):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("SELECT sent_at FROM email_history WHERE user_id=? ORDER BY sent_at DESC", (user_id,))
    rows = c.fetchall()
    conn.close()
    return [row[0] for row in rows]

##########################
# APScheduler for Scheduling (IST-based)
##########################
def scheduled_email_job():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("""
        SELECT id, email, frequency, last_sent, send_time, notifications_paused, num_clippings
        FROM users
    """)
    rows = c.fetchall()
    conn.close()

    # Convert UTC to IST
    now_utc = datetime.utcnow().replace(tzinfo=pytz.utc)
    now_ist = now_utc.astimezone(IST)
    current_time = now_ist.strftime("%H:%M")
    print(f"[Scheduler] Current IST time: {current_time}")

    for row in rows:
        user_obj = User(*row)
        if user_obj.notifications_paused == 1:
            continue
        if current_time != user_obj.send_time:
            continue

        send_flag = False
        if user_obj.frequency == 'daily':
            # (Optional) Prevent double sending if less than 60 seconds have passed
            if user_obj.last_sent:
                last_dt =datetime.fromisoformat(user_obj.last_sent)
                # let's say 30 minutes lockout to avoid duplicates too soon
                if (now_ist - last_dt).total_seconds() < 1800:
                    # skip if last_sent was within 30 minutes
                    continue
            send_flag = True

        elif user_obj.frequency == 'weekly':
            if now_ist.weekday() == 0:
                if user_obj.last_sent:
                    last_dt = datetime.datetime.fromisoformat(user_obj.last_sent)
                    if (now_ist - last_dt).total_seconds() < 60:
                        continue
                    if last_dt.isocalendar()[1] < now_ist.isocalendar()[1]:
                        send_flag = True
                else:
                    send_flag = True
        elif user_obj.frequency == 'monthly':
            if now_ist.day == 1:
                if user_obj.last_sent:
                    last_dt = datetime.datetime.fromisoformat(user_obj.last_sent)
                    if (now_ist - last_dt).total_seconds() < 60:
                        continue
                    if (last_dt.month < now_ist.month) or (last_dt.year < now_ist.year):
                        send_flag = True
                else:
                    send_flag = True

        if send_flag:
            content = generate_email_content(user_obj.id, num_clippings=user_obj.num_clippings)
            if send_email_to_user(user_obj, content):
                conn2 = sqlite3.connect('users.db')
                c2 = conn2.cursor()
                c2.execute("INSERT INTO email_history (user_id, sent_at) VALUES (?, ?)",
                           (user_obj.id, now_ist.isoformat()))
                c2.execute("UPDATE users SET last_sent=? WHERE id=?",
                           (now_ist.isoformat(), user_obj.id))
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
            c.execute("INSERT INTO users (email, password, frequency) VALUES (?, ?, ?)",
                      (email, hashed_pw, freq))
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
        c.execute("""
            SELECT id, email, password, frequency, last_sent, send_time, notifications_paused, num_clippings
            FROM users
            WHERE email=?
        """, (email,))
        row = c.fetchone()
        conn.close()
        if row is None:
            flash("Account does not exist. Please sign up.", "danger")
        else:
            user_id, user_email, user_pw, freq, last_sent, stime, paused, num_clips = row
            if not bcrypt.check_password_hash(user_pw, password):
                flash("Wrong password. Please try again.", "danger")
            else:
                user_obj = User(user_id, user_email, freq, last_sent, stime, paused, num_clips)
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

    history = get_email_history(current_user.id)

    return render_template("dashboard.html",
                           file_display=file_display,
                           total=total,
                           most_highlighted=most_book,
                           chart_labels=labels,
                           chart_counts=counts,
                           email_history=history)

@app.route("/browse")
@login_required
def browse():
    query = request.args.get("query", "").strip().lower()
    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    c.execute("SELECT s3_key FROM clippings WHERE user_id=?", (current_user.id,))
    row = c.fetchone()
    conn.close()

    books = {}
    if row:
        s3_key = row[0]
        try:
            raw_clips = read_clippings_from_s3(s3_key)
            for clip in raw_clips:
                lines = [ln.strip() for ln in clip.split('\n') if ln.strip()]
                # Skip invalid entries
                if len(lines) < 2:
                    continue
                book_title = lines[0]
                # If the user typed a query, filter out books that don't match
                if query and query not in book_title.lower():
                    continue
                snippet = separate_clipping(clip)
                books.setdefault(book_title, []).append(snippet)
        except Exception as e:
            flash(f"Error reading file: {e}", "danger")
    return render_template("browse.html", grouped_books=books)

@app.route("/update_scheduling", methods=["POST"])
@login_required
def update_scheduling():
    stime = request.form.get("send_time", "09:00")
    paused = 1 if request.form.get("notifications_paused") == "on" else 0
    try:
        num_clips = int(request.form.get("num_clippings", 5))
    except ValueError:
        num_clips = 5

    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    c.execute("""
        UPDATE users
        SET send_time=?, notifications_paused=?, num_clippings=?
        WHERE id=?
    """, (stime, paused, num_clips, current_user.id))
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
        s3_key = upload_file_to_s3(f, fname, current_user.id)

        conn = sqlite3.connect("users.db")
        c = conn.cursor()
        c.execute("DELETE FROM clippings WHERE user_id=?", (current_user.id,))
        c.execute("INSERT INTO clippings (user_id, s3_key) VALUES (?, ?)",
                  (current_user.id, s3_key))
        conn.commit()
        conn.close()

        flash("File uploaded & stored successfully!", "success")
    return redirect(url_for("dashboard"))
@app.template_filter('escapejs')
def escapejs_filter(s):
    if not isinstance(s, str):
        s = str(s)
    # First escape any backslashes, then escape quotes
    return s.replace('\\', '\\\\').replace("'", "\\'").replace('"', '\\"')

@app.route("/send-now", methods=["POST"])
@login_required
def send_now():
    # Retrieve from DB
    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    c.execute("""
        SELECT num_clippings
        FROM users
        WHERE id=?
    """, (current_user.id,))
    row = c.fetchone()
    conn.close()

    if row:
        user_num_clips = row[0] or 5
    else:
        user_num_clips = 5

    # Optional override from form
    try:
        n = int(request.form.get("num_clippings", user_num_clips))
    except ValueError:
        n = user_num_clips

    email_html = generate_email_content(current_user.id, num_clippings=n)
    if send_email_to_user(current_user, email_html):
        conn2 = sqlite3.connect("users.db")
        c2 = conn2.cursor()
        now_ist = datetime.utcnow().replace(tzinfo=pytz.utc).astimezone(IST)
        c2.execute("INSERT INTO email_history (user_id, sent_at) VALUES (?, ?)",
                   (current_user.id, now_ist.isoformat()))
        c2.execute("UPDATE users SET last_sent=? WHERE id=?",
                   (now_ist.isoformat(), current_user.id))
        conn2.commit()
        conn2.close()

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
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

if __name__ == "__main__":
    app.run(debug=True)
