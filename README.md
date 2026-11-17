# Train2Conquer

Train2Conquer is a full-stack fitness web application built with Flask
and Python. It helps users create personalized workout plans, track
their progress, explore nutrition guidance, and find trainers based on
gender and location.

The project also uses Google's Gemini API to generate personalized
workout plans from the user's fitness profile.

## Features

-   User registration and login
-   Email OTP verification
-   Fitness profile setup
-   Personalized AI-generated workout plans
-   Workout progress tracking
-   Exercise timers and rest periods
-   Workout navigation with previous/next/skip controls
-   Nutrition information
-   Find trainers by gender and location
-   Trainer application form
-   Contact form with email delivery
-   Account deletion
-   Responsive design for desktop, tablet, and mobile
-   Shared navigation, footer, page loader, and flash messages
-   PostgreSQL database support
-   Environment-variable based configuration
-   Basic security headers and CSRF protection

## Paid plans, payments and costs (production edition)

| Plan | Price | Built by |
|---|---|---|
| Basic | Free | Gemini |
| Personalized | Rs 5 | OpenAI model set in `PERSONALIZED_PLAN_MODEL` |
| Advanced | Rs 15 | OpenAI model set in `ADVANCED_PLAN_MODEL` |

Prices live on the server (`config.py`, 500 and 1500 paise). The browser can never change them. Customers never see model or provider names.

**How a paid plan works:** customer picks a plan -> server creates a Razorpay order at the server price -> Razorpay Checkout opens -> after paying, the server checks Razorpay's signature, then asks Razorpay directly whether that payment is *captured*, for the right *order*, *amount* and *INR* -> only then it saves the payment, queues the plan, builds it with OpenAI and saves it. If the customer closes the browser after paying, Razorpay's **webhook** (`/razorpay/webhook`) does the same work, so the money is never lost. One payment can only ever make one plan. If building fails, the customer retries **without paying again**, or requests a refund which **you approve by hand** in the admin page (nothing is ever refunded automatically).

**Money and OpenAI are separate.** Razorpay pays the customer's money (minus its fees) to your bank account on its own schedule. OpenAI separately bills you for the tokens used. The app records every OpenAI request (tokens, cost in USD and INR, success/failure) and shows the difference in **Admin -> Financial Overview**. Those figures are *estimates calculated from records*, not your bank balance and not Razorpay settlements. Unknown values are shown as "unknown", never as zero.

---

## Beginner guide

### A. Run it on your own computer (test mode)
1. Install Python 3.12+. In the project folder: `pip install -r requirements.txt`
2. Copy `.env.example` to `.env`. Fill in the values after `=`. For your computer use `APP_ENV=development`, a **Razorpay TEST key** (`rzp_test_...`) and, for a quick start, `SUPABASE=sqlite:///local.db`.
3. Start it: `python main.py` (or `flask --app main run`). The database upgrades itself automatically in development.
4. Open http://127.0.0.1:5002

### B. Checklist: where each secret goes
Put these in the **Environment Variables** page of your host (Render: service -> *Environment*). On your own computer they go in `.env`. **Never** put them in code or on GitHub.

| What | Variable name |
|---|---|
| Mode | `APP_ENV` = `production` |
| Website secret | `SECRET_KEY` (long random text) |
| Database URL | `SUPABASE` |
| Razorpay **Live** Key ID (starts `rzp_live_`) | `RAZORPAY_KEY_ID` |
| Razorpay **Live** Key Secret | `RAZORPAY_KEY_SECRET` |
| Razorpay **Live** Webhook Secret | `RAZORPAY_WEBHOOK_SECRET` |
| OpenAI key | `OPENAI_API_KEY` |
| Personalized model | `PERSONALIZED_PLAN_MODEL` (`gpt-6-luna`) |
| Advanced model | `ADVANCED_PLAN_MODEL` (`gpt-6.1-sol`) |
| Dollar-to-rupee rate | `OPENAI_USD_TO_INR` (e.g. `90`) |
| Gemini key, email login | `GEMINI_API_KEY`, `EMAIL`, `PASSWORD` |

In production the app **refuses** `rzp_test_` keys: payments are switched off (the free plan still works) and Admin shows exactly what is missing.

### C. Back up before upgrading the database (do this first, once per release)
Your existing users and plans are kept: the upgrade only *adds* columns/tables. Still, back up:
- **Supabase:** Dashboard -> *Database* -> *Backups* -> make sure a recent backup exists (or run `pg_dump "YOUR_DATABASE_URL" > backup.sql` on your computer).
- Then run the upgrade (from your computer with `SUPABASE` set to your real database URL, or in the Render *Shell*):
  ```
  python manage.py status
  python manage.py migrate --i-have-a-backup
  ```
In production the website stays paused (shows "being upgraded") until this has been run. (Advanced users may set `AUTO_MIGRATE=1` to let the site upgrade itself on start; that skips the backup step.)

### D. Switch on real Razorpay payments
1. Finish Razorpay **KYC / account activation** (you do this in the Razorpay dashboard; I cannot do it for you).
2. Dashboard -> switch to **Live Mode** -> *Account & Settings -> API Keys -> Generate Live Key*. Copy the Key ID (`rzp_live_...`) and Secret into the variables above.
3. Dashboard (Live Mode) -> *Account & Settings -> Webhooks -> Add new webhook*:
   - URL: `https://YOUR-WEBSITE/razorpay/webhook`
   - Choose a webhook secret (any long random text), type the same text in `RAZORPAY_WEBHOOK_SECRET`.
   - Tick events: `payment.captured`, `order.paid`, `payment.failed`, `refund.processed`, `refund.failed` (and `refund.created` if shown).
4. In Razorpay payment settings keep **auto-capture** on (the app also captures "authorized" payments itself).
5. Redeploy. Open `https://YOUR-WEBSITE/healthz` -> it should say `"environment":"production"` and `"payments_enabled":true`.
6. **First real test:** log in, buy the Rs 5 plan yourself with UPI, confirm the plan appears, then check **Admin -> Financial Report**. If you want your Rs 5 back, use *Request a refund* only works for failed plans; otherwise refund from the Razorpay dashboard (the webhook records it).

### E. Deploy on Render (recommended)
1. Push the project to GitHub (the `.gitignore` already keeps `.env` out).
2. Render -> *New -> Web Service* -> your repo. **Build:** `pip install -r requirements.txt` **Start:** `gunicorn main:app --workers 2 --timeout 120`
3. Add the environment variables from the table (plus `GENERATION_MODE=inline` to start simply).
4. Run the database upgrade once (section C).
5. **Recommended later:** add a second Render service of type *Background Worker* with Start `python worker.py`, the same environment variables, and set `GENERATION_MODE=worker` on **both** services. Plans are then built by the worker, so a website restart never interrupts a paid plan. (An example is in `render.yaml.example`.)
6. Vercel / other serverless hosts **cannot** run reliable background generation; use Render (or any always-on server).

Infrastructure needed: one web service, one PostgreSQL database (Supabase), optionally one background worker. No Redis needed (set `RATELIMIT_STORAGE_URI` to a Redis URL only if you run many web servers).

### F. Safety limits (all optional, in `.env.example`)
`OPENAI_DAILY_LIMIT_USD`, `OPENAI_PER_GENERATION_LIMIT_USD`, `PAID_AI_MAX_ATTEMPTS`, `PAID_AI_MAX_OUTPUT_TOKENS`, `MAX_GENERATION_ATTEMPTS`, `MAX_PAID_PLANS_PER_DAY`. OpenAI's automatic hidden retries are switched off, so every billable request is recorded. After an attempt whose cost is unknown (timeout) the app does not retry automatically.

### G. If model prices change
Edit the table at the top of `costing.py` (or set `MODEL_PRICING_JSON`). A model with no known price is recorded as **unknown cost**, never zero.

### H. Run the tests
`pip install pytest` then `python -m pytest tests -q`. They use fake Razorpay/OpenAI/Gemini responses and a temporary database - **no real money and no real API calls.** See `TEST_REPORT.md`.

### Files
`main.py` routes and job queue | `models.py` tables | `config.py` settings | `billing.py` Razorpay | `ai_paid.py` OpenAI | `costing.py`, `usage.py` cost tracking | `finance.py` admin numbers | `migrations.py`, `manage.py` safe upgrades | `worker.py` background worker | `tests/`

---

## Tech Stack

### Backend

-   Python
-   Flask
-   Flask-SQLAlchemy
-   SQLAlchemy
-   Flask-Login
-   Flask-WTF
-   WTForms
-   Bootstrap-Flask
-   Gunicorn

### Frontend

-   HTML5
-   CSS3
-   JavaScript
-   Bootstrap
-   Jinja2 templates

### Database

-   PostgreSQL
-   SQLAlchemy ORM

### AI and Other Services

-   Google Gemini API
-   Gmail SMTP for OTP and email notifications
-   python-dotenv for environment variables

## Project Structure

``` text
Train2Conquer/
├── main.py              # app setup + public/user routes (auth, workouts, payments, contact, trainer search)
├── extensions.py        # rate limiter (shared, avoids circular imports)
├── config.py            # all environment settings
├── models.py            # all database tables
├── database.py          # database URL / engine setup
├── migrations.py        # numbered, add-only schema upgrades (run via manage.py)
├── manage.py            # `python manage.py migrate` (backs up first)
├── forms.py             # Flask-WTF forms and their validation
├── exercises.py         # exercise names / video catalogue
├── ai.py, ai_paid.py    # Gemini (free plan) and OpenAI (paid plans) generation
├── billing.py, costing.py, usage.py, finance.py, worker.py   # payments, costs, accounting, background worker
├── admin/               # admin panel Blueprint (/admin/...), one guard for every route
├── services/
│   ├── otp.py           # e-mail verification codes (hashed, server-side, rate-limited)
│   ├── trainers.py      # trainer search (online/offline) + contact message
│   ├── accounts.py      # account deletion (shared by user and admin)
│   ├── admin_stats.py   # dashboard numbers and payment status definitions
│   └── audit.py         # admin audit trail
├── templates/ (+ admin/)   static/ (css, js)
└── tests/               # pytest suite (148 tests, all external services mocked)
```

## Admin panel

Open `/admin` while logged in as an admin (set `ADMIN_EMAIL` or edit a user). Pages:
dashboard (counters), users, trainers, workout plans, payments (read-only), finance
(costs/refunds), audit log. Rules: every admin route is guarded in one place
(`admin/routes.py: require_admin`); every change is a CSRF-protected POST; deletes need a typed
confirmation; you cannot deactivate/delete yourself or the last active admin; every important
change is written to the audit log; **payments can never be edited or deleted** and refunds go
only through Razorpay. Deactivated users and trainers keep their data but cannot log in / are hidden.
Because the admin panel is sensitive, a login restored from a Remember Me cookie must confirm by
e-mail code again before it can open `/admin`.

## Login, OTP and Remember Me

Login is passwordless: an e-mail code (6 digits, valid 10 minutes, 5 attempts, one use). Only a
keyed hash of the code is stored in the `otp_codes` table, never in the browser cookie.
The login page says the same thing for known and unknown e-mails.

**Remember Me** (checkbox, unchecked by default) uses Flask-Login's signed `remember_token`
cookie - no extra table. It is created only after the e-mail code is verified. Cookie settings:
HttpOnly, SameSite=Lax, Secure in production, path `/`, no Domain, fixed lifetime
`REMEMBER_ME_DAYS` (default 30, max 90, not extended by visiting). The cookie holds
`<user id>:<login time>` plus an HMAC signature made with `SECRET_KEY`; the server re-checks the
database on every request, so deleted/deactivated users and changed admin rights take effect at
once, and logins older than the lifetime are rejected by the server. Logout clears the cookie.
Sessions restored from the cookie are "not fresh": deleting an account, requesting a refund and
the admin panel ask for a new e-mail code first.

Limitation: the cookie is stateless, so a copy stolen *before* logout stays valid until it
expires or `SECRET_KEY` changes (rotating it signs everybody out). Per-user revocation ("sign out
everywhere") would need one extra column on `users`; it was not added because you asked for no
schema changes for Remember Me.

## How It Works

### 1. Registration and Login

Users create an account using their name and email. The application uses
an OTP verification flow before allowing the user to continue.

Flask-Login manages authenticated user sessions.

### 2. Fitness Profile

After registration, users can provide information such as:

-   Date of birth
-   Height
-   Weight
-   Gender
-   Fitness goal
-   Training experience
-   Number of workout days
-   Available equipment

This information is used when generating the workout plan.

### 3. AI Workout Generation

The fitness profile is sent to the Gemini API through `ai.py`.

The AI is instructed to return a structured JSON workout plan using the
exercises and images available in the application.

The generated plan is stored in the database and displayed through the
workout interface.

The application also supports background generation so the HTTP request
does not have to wait for the complete AI response.

### 4. Workout Tracking

Users can open their workout plan and complete exercises using the
workout interface.

The workout JavaScript handles features such as:

-   Exercise navigation
-   Timed exercises
-   Rest timers
-   Pause/resume
-   Skip
-   Previous/next controls
-   Progress tracking
-   Workout completion

### 5. Trainer Search

Users can search for trainers using information such as gender and
location.

Trainer results use latitude and longitude data to calculate distance
between the user and trainers.

### 6. Email Features

The application uses Gmail SMTP for:

-   Account/login OTPs
-   Contact form messages
-   Trainer applications

Email credentials are loaded from environment variables instead of being
stored directly in the source code.

## Requirements

Before running the project, install:

-   Python 3.12+ recommended
-   PostgreSQL
-   A Google Gemini API key
-   A Gmail account configured for SMTP access

## Installation

### 1. Clone the repository

``` bash
git clone <your-repository-url>
cd Train2Conquer
```

### 2. Create a virtual environment

Windows:

``` bash
python -m venv .venv
.venv\Scripts\activate
```

macOS/Linux:

``` bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

``` bash
pip install -r requirements.txt
```

## Environment Variables

Create a `.env` file in the project root.

Copy `.env.example` to `.env` and fill it in. It lists every variable
(Razorpay, OpenAI, models, exchange rate, limits). See the **Beginner guide**
above, section B.

Do not commit your `.env` file to GitHub.

Your actual environment variable names must match the ones used by the
application.

## Database

Train2Conquer uses SQLAlchemy with PostgreSQL.

The application reads the database connection string from:

``` text
SUPABASE
```

Database upgrades are numbered steps in `migrations.py`, run with
`python manage.py migrate` (development upgrades itself automatically;
production waits for you to run it after a backup - see section C).

The main models (`models.py`) include `User`, `Trainer`, `FitnessProfile`,
`WorkoutPlan`, `Payment`, `PaymentEvent` and `AIUsage`.

## Running Locally

Activate your virtual environment and run:

``` bash
python main.py
```

The application will normally be available at:

``` text
http://127.0.0.1:5002
```

The exact port can depend on the configuration in the project.

## Production Deployment

The project includes Gunicorn for production deployment.

Start command: `gunicorn main:app --workers 2 --timeout 120`
(optional worker: `python worker.py`). Set every variable from the table in
section B of the Beginner guide on your hosting platform and set
`APP_ENV=production`.

Never place API keys, passwords, database credentials, or other secrets
directly inside the source code.

## Security

Implemented (see `REFACTOR_REPORT.md` for the audit and what remains):

-   CSRF protection on every state-changing request (Flask-WTF)
-   Flask-Login; HttpOnly, SameSite=Lax, Secure (production) cookies
-   Verification codes stored hashed on the server with attempt/expiry/rate limits
-   No account enumeration on login/register
-   Server-side authorization for all admin routes, audit log, safeguards for the last admin
-   Razorpay signature verification, server-side amounts, idempotent processing (existing)
-   Security headers (nosniff, frame-ancestors, referrer/permissions policy, HSTS in production,
    `no-store` on private pages)
-   Secrets only in environment variables

For production, use a strong random `SECRET_KEY` and keep all credentials outside the repository.

## Main Application Files

### `main.py`

Contains the Flask application, database models, authentication, routes,
email functions, trainer search, workout plan handling, and application
configuration.

### `ai.py`

Handles communication with Google's Gemini API and contains the
workout-generation instructions and available exercise/image data.

### `forms.py`

Contains the WTForms used by the application, including registration,
login, OTP, fitness profile, trainer search, trainer application, and
contact forms.

### `static/js/workout.js`

Controls the interactive workout experience, including timers,
navigation, exercise completion, and workout progress.

### `static/css/main.css`

Contains styles shared across the application, such as the navbar,
footer, loader, flash messages, and other common components.

### `static/css/pages.css`

Contains styles specific to individual pages.

## Future Improvements

Possible future improvements include:

-   Trainer accounts and dashboards
-   Trainer/user messaging
-   More detailed progress analytics
-   Workout history
-   Exercise video demonstrations
-   Better AI plan regeneration controls
-   Cloud image storage
-   Automated database migrations
-   Automated testing
-   Improved production email delivery
-   Mobile application version

## License

This project is currently a personal/portfolio project.

If you plan to publish or distribute it publicly, add the license that
matches how you want others to use the project.

## Author

**Syed Matheen**

Train2Conquer was built as a full-stack fitness project combining Flask,
PostgreSQL, JavaScript, responsive web design, and AI-powered workout
generation.
