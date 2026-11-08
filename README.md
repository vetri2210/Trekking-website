# Trailwise · Trekking Management Application

A locally runnable trek-management app for trekkers, trek staff, and administrators. The interface is Vue 3 with Bootstrap; Flask serves the API and frontend; SQLite is initialized by the application. Redis powers trek-list caching and the Celery broker/result backend.

## What is included

- Trekkers can register, update their profile, explore/filter open treks, book or cancel a place, and view their full booking history.
- Admins can manage treks and route status, create staff accounts, assign trail leads, manage user/staff status, and view booking history and dashboard totals.
- Staff can see their assigned treks and participants, update remaining places and trek status, and mark treks complete.
- Booking uses a SQLite write transaction to prevent overbooking; a trek must be open, in the future, and not already actively booked by that trekker.
- Redis caches the unfiltered public trek list for five minutes. If Redis is temporarily unavailable, the app logs the issue and serves the trek list from SQLite.
- Celery supports daily trek reminders at 8:00 AM, a monthly HTML admin report on the first of each month at 9:00 AM (Asia/Kolkata), and user-triggered CSV exports.

## Requirements

- Python 3.10+
- Redis server
- A modern browser with an internet connection for the Vue, Bootstrap, and image CDNs

## Run locally (Windows PowerShell)

1. Start a local Redis server on `localhost:6379`.
2. Install the Python requirements:

   ```powershell
   python -m pip install -r backend\requirements.txt
   ```

3. Optional: set a private admin password and session key before the first launch:

   ```powershell
   $env:TMA_ADMIN_EMAIL = "admin@example.com"
   $env:TMA_ADMIN_PASSWORD = "choose-a-private-password"
   $env:TMA_SECRET_KEY = "choose-a-long-random-secret"
   ```

   If not overridden, the first-run local admin account is `admin@trailwise.local` / `TrailAdmin!2026`. Set a private password for any shared or deployed environment. The admin is created programmatically when the database is initialized; no admin registration route exists.

4. Start the Flask app:

   ```powershell
   python backend\app.py
   ```

   Visit `http://127.0.0.1:5000`. SQLite is created automatically at `backend\trekking.db` on first launch, and a few example open treks are added for the local demo.

5. In a separate PowerShell window, start the Celery worker and scheduler from the backend folder:

   ```powershell
   Set-Location backend
   celery -A tasks.celery worker --pool=solo --loglevel=info
   ```

   ```powershell
   Set-Location backend
   celery -A tasks.celery beat --loglevel=info
   ```

   The `solo` worker pool is suitable for the local Windows demo. Keep both processes running for CSV exports and scheduled jobs.

## Optional notifications and email

- Set `GOOGLE_CHAT_WEBHOOK_URL` to send the daily reminders to a Google Chat incoming webhook. Without it, reminders are written to the worker log.
- Set `SMTP_HOST`, and optionally `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, and `SMTP_FROM`, to email the monthly HTML report to the pre-created admin account. Without SMTP configuration, the report is generated and logged, but not delivered.
- Set `REDIS_URL` if Redis is not at `redis://localhost:6379/0`.
- Set `TMA_DATABASE` to use a different SQLite file path.

## Project layout

```text
backend/
  app.py             Flask routes, session auth, role checks and API
  db.py              SQLite schema, connections and first-run admin/seed data
  tasks.py           Celery export, reminder and monthly-report jobs
  requirements.txt
frontend/
  index.html         Vue/Bootstrap entry point
  app.js             Vue interface and API interactions
  styles.css         Responsive Trailwise visual design
```
