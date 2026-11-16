# Files changed (compared with your uploaded Train2Conquer-fixed ZIP)

Your original ZIP is kept untouched as `Train2Conquer-ORIGINAL-backup.zip`.

## New files
- `config.py` - all environment settings; production = live keys only; prices (server side)
- `models.py` - all tables (moved out of main.py) + new `PaymentEvent`, `AIUsage`, new columns
- `database.py` - database URL/engine setup (moved out of main.py, same logic)
- `migrations.py`, `manage.py` - numbered, add-only, backup-first database upgrades
- `costing.py` - model price table, exact Decimal cost maths
- `usage.py` - saves every OpenAI attempt, daily totals, per-plan totals
- `finance.py` - numbers for the admin Financial Overview
- `worker.py` - background worker for plan generation
- `templates/admin/finance.html`, `_finance_summary.html`, `_env_banner.html`
- `tests/` (72 tests), `TEST_REPORT.md`, `CHANGES.md`, `render.yaml.example`

## Changed files
- `main.py` - rebuilt payment + generation + admin sections (database-backed job queue, webhook with event de-duplication, late-payment handling, refund request/approve flow, rate limiting, secure cookies, upgrade gate); authentication, profile, trainers, workout and other routes are unchanged
- `billing.py` - live/test strictness, refund call, raw-body webhook check
- `ai_paid.py` - records every attempt (before JSON validation), spend limits, no hidden SDK retries
- `requirements.txt` - added Flask-Limiter
- `.env.example`, `README.md`
- `templates/_plan_options.html`, `static/js/plans.js`, `static/css/pages/plans.css` - progress states, refund button
- `templates/admin/dashboard.html`, `static/css/admin.css` - Financial Overview section

## Removed
- `test_plans_e2e.py` (old script that tested functions that moved; replaced by `tests/`)

## Not changed
All other templates, CSS, JS, videos/images, `ai.py` (Gemini), `forms.py`.
