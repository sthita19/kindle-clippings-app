# Kindle Clippings

Kindle Clippings is a Flask-based web application that lets you upload your Kindle highlights and clippings, browse them, and receive them by email on a scheduled basis.

## Overview

This application is designed to help you manage your Kindle clippings. Users can:
- Create an account and log in securely.
- Upload a file containing Kindle highlights (stored in AWS S3).
- Browse and search clippings by book title.
- View statistics and analytics (total clippings, most highlighted book, email send history) on a dashboard.
- Configure email notifications with options for frequency (daily, weekly, monthly), send time, and number of clippings per email.
- Receive automated emails of selected clippings via a scheduled background job.

## Features

- **User Authentication:** Sign up, log in, and log out using Flask-Login and Flask-Bcrypt.
- **File Upload:** Securely upload and store Kindle clippings files in AWS S3.
- **Dashboard:** View clippings analytics, email send history, and scheduling options.
- **Browse Clippings:** Search and display clippings by book title with a responsive modal view.
- **Email Notifications:** Schedule and send emails with a random selection of your clippings.
- **Scheduling:** Uses APScheduler to automate email sending based on user preferences.
- **Responsive UI:** Built with Tailwind CSS and Jinja2 templates for a modern look.
## Screenshots

### Home Page
![image](https://github.com/user-attachments/assets/011f3f76-720a-427b-ad5e-c845b6bfab12)

### Dashboard
![image](https://github.com/user-attachments/assets/70ac328c-94fc-4a61-b438-dab71f380d09)
![image](https://github.com/user-attachments/assets/b7f2d7cb-b953-410b-9644-309fac1fc4fc)


### Browse Clippings
![image](https://github.com/user-attachments/assets/ef4a7dc2-2e78-4bdd-ba62-b60e4c73b375)
![image](https://github.com/user-attachments/assets/1ca7ee84-cd6f-4f72-9572-d22e00a5c279)

### Authentication
![image](https://github.com/user-attachments/assets/caa9b286-b8c7-4261-a53c-da8e20a8e931) ![image](https://github.com/user-attachments/assets/09359887-bf92-4512-8985-aa76be23f115)



## Technology Stack

- **Backend:** Python, Flask, SQLite, APScheduler
- **Frontend:** HTML, Jinja2, Tailwind CSS
- **Authentication:** Flask-Login, Flask-Bcrypt
- **Cloud Storage:** AWS S3 (using boto3)
- **Email:** SMTP (configured for Gmail)

## Installation

1. **Clone the Repository:**

   ```bash
   git clone https://github.com/sthita19/kindle-clippings.git
   cd kindle-clippings
   ```

2. **Set Up a Virtual Environment:**

   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install Dependencies:**

   If a `requirements.txt` is provided:
   ```bash
   pip install -r requirements.txt
   ```
   Otherwise, install the following packages manually:
   - Flask
   - Flask-Login
   - Flask-Bcrypt
   - APScheduler
   - boto3
   - pytz

4. **Configure Environment Variables:**

   Create a `.env` file or set the following environment variables:
   - `FLASK_SECRET_KEY`: Your Flask secret key.
   - `AWS_BUCKET_NAME`: Your AWS S3 bucket name.
   - `AWS_REGION`: AWS region (e.g., `ap-south-1`).
   - `SENDER_EMAIL`: The email address used for sending notifications.
   - `SENDER_PASSWORD`: The SMTP or app password for the sender email.

5. **Initialize the Database:**

   The application automatically initializes the SQLite database (`users.db`) on the first run.

6. **Run the Application:**

   ```bash
   python app.py
   ```
   Then open your browser and navigate to [http://localhost:5000](http://localhost:5000).

## Project Structure

```
kindle-clippings/
├── app.py
├── users.db
├── templates/
│   ├── base.html
│   ├── home.html
│   ├── login.html
│   ├── signup.html
│   ├── dashboard.html
│   └── browse.html
├── uploads/
└── README.md
```

- **app.py:** Contains the main application logic including routes, scheduling, and email handling.
- **templates/:** Jinja2 HTML templates for the UI.
- **uploads/:** Directory for temporarily storing uploaded files.
- **users.db:** SQLite database that stores user data, clippings metadata, and email history.

## Usage

1. **Sign Up / Login:**  
   Create a new account or log in using your registered email and password.

2. **Upload Clippings:**  
   After logging in, go to the Dashboard to upload your Kindle clippings file. This file will be stored on AWS S3.

3. **Browse Clippings:**  
   Use the Browse page to search for clippings by book title. Click on a book tile to view its highlights in a modal.

4. **Configure Email Notifications:**  
   From the Dashboard, set your email frequency (daily, weekly, monthly), select your preferred send time, and specify the number of clippings per email.  
   You can also trigger an immediate email send using the "Send Now" option.

5. **Automated Scheduling:**  
   The app uses APScheduler to automatically send email notifications based on your scheduling preferences.

## Contributing

Contributions are welcome! Please fork the repository, make your changes, and submit a pull request.

## License

This project is licensed under the MIT License.

## Acknowledgments

- Built with [Flask](https://flask.palletsprojects.com/).
- Styled with [Tailwind CSS](https://tailwindcss.com/).
- AWS S3 integration via [boto3](https://boto3.amazonaws.com/).
