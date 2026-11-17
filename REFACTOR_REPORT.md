# Train2Conquer - refactor, security and feature report

Branch: `refactor/professional-cleanup` (baseline commit = your uploaded ZIP; a copy of the
original folder was also kept outside the project). Test suite: **148 passed, 0 failed**
(was 72 at the start). The app was also started with gunicorn on a fresh database and driven
over real HTTP (see "Live checks").

## A. Architecture (what changed, honestly)

| Area | Status |
|---|---|
| `admin/` Blueprint (all `/admin` routes, one `before_request` guard) | **done** (moved out of main.py, rewritten) |
| `services/` (otp, trainers, accounts, admin_stats, audit) | **done** |
| `extensions.py`, `exercises.py` extracted from main.py | **done** |
| Models/config/migrations/forms already separate | kept |
| **Auth, workout, payment, contact, trainer-search routes still live in `main.py`** (1,662 lines, was 2,094) | **NOT split into blueprints** |

Why: those routes handle money and sign-in, are tightly coupled to ~70 existing tests that patch
`main.*`, and splitting them is a mechanical but risky change that is best done as its own step
with these tests as a safety net. The suggested `app/` package layout was **not** adopted: it would
change the `main:app` entry point your Render deployment uses. Nothing was renamed in the database.

Data model (relevant relationships): `users` 1-1 `profiles`; `users` 1-N `workout_plans`; `users` 1-N
`payments` (nullable, detached on account deletion); `payments` 1-N `workout_plans` (by `payment_id`),
`ai_usage`, `payment_events` (by order id); `trainers` standalone; new `otp_codes` and
`admin_audit_log` (no foreign keys on purpose).

## B. Files
Created: `admin/__init__.py`, `admin/routes.py`, `services/*.py`, `extensions.py`, `exercises.py`,
`templates/admin/{_layout,_macros,_plan_days,plans,plan_detail,payments,payment_detail,audit_log}.html`
(`_env_banner.html`/`_finance_summary.html` are included by the dashboard - see Known gaps), tests
`test_auth_otp.py`, `test_trainers.py`, `test_admin.py`, `test_remember_me.py`, `test_security_headers.py`,
`REFACTOR_REPORT.md`.
Modified: `main.py`, `models.py`, `migrations.py` (007), `forms.py`, `config.py`, admin templates, results/contact
templates + CSS, `README.md`, `.env.example`, `.gitignore`, tests/conftest.
Removed: old admin code from `main.py`, duplicated delete-account logic, dead imports, `print()` debugging (now logger).

## C. Features
* **Online trainer search fixed.** Cause: the form required a location, so online searches with the
  (hidden) empty field silently failed validation and re-rendered the same page. Now: location optional
  online, no geocoding, no radius, all active trainers of the profile's gender, 12 per page, empty state.
  Offline keeps the 5 km Haversine search; location required; geocoder outage handled (was a 500).
  Search is now POST -> redirect -> GET so pages are linkable.
* **Contact prefill.** "Contact Trainer" links `?trainer=<id>&mode=`; the server loads the trainer, builds the
  message (trainer name; location sentence only offline), user can edit. On send the server re-validates the
  trainer id (inactive/unknown/tampered ids are refused) and the message length.
* **Admin panel:** dashboard counters, users (search, status/role filters, detail with plans and payments,
  edit, activate/deactivate, delete with typed e-mail), workout plans (list, filters, detail, delete),
  payments (read-only list/search/filters/detail incl. gateway refs), trainers (filters, edit, activate,
  delete), audit log, existing finance/refund page kept.
* **Remember Me** (section below).

## D. Security report
| # | Finding | Severity | Status |
|---|---|---|---|
| 1 | **OTP stored in the session cookie (readable, not encrypted): anyone could log in as any e-mail** (reproduced on the original code) | **Critical** | Fixed: hashed code in DB, attempts counted server-side |
| 2 | Attempt counter/cooldown in the cookie (resettable by replaying a cookie) | High | Fixed: enforced in DB |
| 3 | Account enumeration on login/register | Medium | Fixed: same response + decoy codes |
| 4 | Trainer-id crash (500) on tampered contact form; geocoder outage 500 | Low | Fixed |
| 5 | No rate limits on login/OTP/contact routes | Medium | Fixed (per-minute and per-hour) |
| 6 | Admin could not suspend users; no audit trail; admin guard was a per-route decorator | Medium | Fixed (guard on blueprint, audit log, safeguards) |
| 7 | Private pages cacheable after logout; missing Permissions-Policy/CSP basics/HSTS | Low | Fixed |
| 8 | Contact form validation depended on client-sent hidden name/e-mail for logged-in users | Low | Fixed |

Remaining risks: e-mail sending time can still differ for real vs unknown addresses (timing
enumeration); CSP allows inline scripts (strict script-src needs a nonce rollout); in-memory rate limiter
is per process (use `RATELIMIT_STORAGE_URI=redis://...` with more than one worker); the contact/trainer-
application e-mails use Gmail SMTP with a password in env; payment, webhook and AI-generation code was
**not re-audited line by line this session** (only exercised through the existing 72 tests); no penetration test.

## Remember Me - exactly how it works
Flask-Login `login_user(user, remember=choice)`. The choice is stored in the session when the e-mail form is
submitted and used only after the code is verified. Cookie `remember_token`: HttpOnly, SameSite=Lax, Secure
(production), Path=/, no Domain, `REMEMBER_ME_DAYS` (30). Payload `<id>:<login time>` + HMAC(SECRET_KEY); Flask-Login's
cookie has no timestamp, so `User.get_id()` embeds it and `load_user` enforces the lifetime server-side. Every
request re-reads the user (deleted/deactivated -> logged out; admin rights from the database). Logout clears
it. Restored sessions are "not fresh": delete-account, refund request and the admin panel require a new e-mail
code. No new table, no browser storage.
Limitations: stateless cookie (a copy stolen before logout works until expiry or `SECRET_KEY` rotation);
changing a user's e-mail does not revoke their cookie; there are no passwords in this app, so "password
change" does not exist. A per-user `session_version` column would fix revocation - **not added, awaiting your decision.**

## E. Testing
148 pytest tests passed (OTP, cookies, tampering, expiry, logout, deactivation, admin guard/safeguards/audit, payments
read-only, trainer search, contact, headers, migration on an old database). Regression test added for every bug above.
Live (gunicorn + curl, SQLite): register with Remember Me -> cookie expires in 30 days -> browser restart keeps login
-> logout clears it; all admin pages return 200 for a fresh admin login; online search without location lists trainers;
prefilled message correct; offline blank location shows the error.
**Not tested:** PostgreSQL/Supabase (tests and live run used SQLite; migration SQL is standard but unverified there);
real Razorpay, OpenAI, Gemini, SMTP, Nominatim (all mocked); real browsers/Playwright (not available); mobile layout,
accessibility, visual review of the new admin pages; load/performance.

## F. Deployment
New env vars (both optional): `REMEMBER_ME_DAYS`, `RATELIMIT_STORAGE_URI`. No new dependencies.
1. Back up the database. 2. `python manage.py migrate` (applies **007**: adds `users.is_active`, `users.created_at`,
`trainers.is_active`, `trainers.contact_email`, tables `otp_codes`, `admin_audit_log`; add-only, everyone stays active).
3. Deploy (`main:app` unchanged). 4. Smoke test login, admin, find trainer. Existing logged-in sessions stay valid.
**Decision for you:** if you fill a trainer's "contact e-mail" in admin, the user's name, e-mail and message are sent
to that trainer (the contact page tells the user). Left empty, messages go to the site inbox with the trainer's name/id,
as before. Trainers have no e-mail field otherwise, so without it a message cannot reach a trainer directly.

## G. Assessment
Working: everything above, all 148 tests. Improved: the critical login flaw, admin control, trainer discovery/contact.
Still needs attention: split the rest of `main.py` into blueprints; a Postgres test run; review of payment/webhook/AI
code; frontend consolidation (repeated CSS, JS organisation, accessibility pass) was **not done**; trainer
*applications* are only e-mailed (no table), so there is no approve/reject queue - adding one needs a new table and your approval.
**Ready to deploy?** Reasonably, after a backup, a Postgres staging run of migration 007 and a manual click-through.
I would not call the app "fully secure" - see remaining risks.
