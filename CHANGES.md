# Professional upgrade (admin, payments, OTP, equipment, Gemini-only, plan replacement)

**Database:** one new migration, `008 plan_replacement` (adds `workout_plans.is_archived` and `profile_hash`; backfills
existing plans; additive and safe to re-run). Run `python manage.py migrate` (it backs up first).

**Environment:** removed `OPENAI_API_KEY`, `OPENAI_USD_TO_INR`, `OPENAI_DAILY_LIMIT_USD`, `OPENAI_PER_GENERATION_LIMIT_USD`,
`OPENAI_DAILY_MAX_REQUESTS`, `PERSONALIZED_PLAN_MODEL`, `ADVANCED_PLAN_MODEL`, `PAID_AI_REASONING_EFFORT` (delete them from your host).
Added (all optional): `GEMINI_MODEL`, `GEMINI_DAILY_MAX_REQUESTS`, `GEMINI_MAX_TRIES`, `ADMIN_REQUIRE_FRESH_LOGIN` (`writes` default | `always`).

- OTP e-mail redesigned (`services/emails.py`): HTML + plain text, inline logo, never logs the code.
- Admin panel: one design system (tokens in `main.css`, `admin.css`), sidebar/tab navigation, responsive tables, reusable confirm dialog
  and toasts; blue lines were Bootstrap's default link colour/underline on dashboard stat cards.
- Admin auth: viewing admin pages no longer needs a fresh e-mail code; changing things still does. Normal users get a 403 page.
- Payments: Retry (same order) / Try another payment method (fresh order) / refund unchanged; server reconciles with Razorpay first.
- Equipment: Gym + No equipment + individual items (`equipment.py`), enforced in the form, the AI exercise list and plan validation.
  Fixed a bug where editing equipment on an existing profile was silently ignored.
- All plans use Gemini via `gemini_service.py` + `plan_generator.py`; OpenAI removed.
- Names validated on registration/contact/admin forms; fitness-profile minimum age 15 (`validation.py`).
- Editing the profile makes the next plan REPLACE the active one (`plan_cycle.py`); old plans are archived, never deleted.

---

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
- `plan_generator.py` - records every attempt (before JSON validation), spend limits, no hidden SDK retries
- `requirements.txt` - added Flask-Limiter
- `.env.example`, `README.md`
- `templates/_plan_options.html`, `static/js/plans.js`, `static/css/pages/plans.css` - progress states, refund button
- `templates/admin/dashboard.html`, `static/css/admin.css` - Financial Overview section

## Removed
- `test_plans_e2e.py` (old script that tested functions that moved; replaced by `tests/`)

## Not changed
All other templates, CSS, JS, videos/images, `ai.py` (Gemini), `forms.py`.
