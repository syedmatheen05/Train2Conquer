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
├── main.py
├── ai.py
├── forms.py
├── requirements.txt
├── templates/
│   ├── base.html
│   ├── navbar.html
│   ├── footer.html
│   ├── loader.html
│   ├── home.html
│   ├── register.html
│   ├── login.html
│   ├── verify_otp.html
│   ├── verify_contact.html
│   ├── fitness-profile.html
│   ├── dashboard.html
│   ├── workout.html
│   ├── nutrition.html
│   ├── find_trainer.html
│   ├── trainer_results.html
│   ├── become_trainer.html
│   ├── contact.html
│   ├── about.html
│   ├── privacy.html
│   └── error.html
└── static/
    ├── css/
    │   ├── main.css
    │   └── pages.css
    └── js/
        ├── main.js
        ├── loader.js
        └── workout.js
```

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

Example:

``` env
SECRET_KEY=your-secret-key
SUPABASE=your-postgresql-connection-string
GEMINI_API_KEY=your-gemini-api-key
EMAIL=your-gmail-address
PASSWORD=your-gmail-app-password
```

Do not commit your `.env` file to GitHub.

Your actual environment variable names must match the ones used by the
application.

## Database

Train2Conquer uses SQLAlchemy with PostgreSQL.

The application reads the database connection string from:

``` text
SUPABASE
```

The database tables are created/checked when the application starts.

The main models include:

-   `User`
-   `Trainer`
-   `FitnessProfile`
-   `WorkoutPlan`

## Running Locally

Activate your virtual environment and run:

``` bash
python main.py
```

The application will normally be available at:

``` text
http://127.0.0.1:5000
```

The exact port can depend on the configuration in the project.

## Production Deployment

The project includes Gunicorn for production deployment.

A typical start command is:

``` bash
gunicorn main:app
```

Before deploying, make sure the following environment variables are
configured on your hosting platform:

``` text
SECRET_KEY
SUPABASE
GEMINI_API_KEY
EMAIL
PASSWORD
```

Never place API keys, passwords, database credentials, or other secrets
directly inside the source code.

## Security

The application includes:

-   CSRF protection
-   Flask-Login authentication
-   HTTP-only session cookies
-   SameSite cookie configuration
-   Production secure cookies
-   Security response headers
-   Environment-based secrets
-   HTML escaping for user-provided email content

For production, use a strong randomly generated `SECRET_KEY` and keep
all credentials outside the repository.

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
