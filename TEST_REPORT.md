# Train2Conquer - Test Report

**Result: 72 automated tests, all passing** (`python -m pytest tests -q`), run in a Linux sandbox (Python 3.12).

## What was and was NOT tested - please read

| Area | How it was tested | Real service touched? |
|---|---|---|
| Razorpay **signature checks** (payment + webhook) | Real HMAC code, fake secrets | No |
| Razorpay order/fetch/capture/refund **network calls** | Replaced by a fake gateway | **No** |
| OpenAI generation, tokens, retries, timeouts | Fake OpenAI client returning scripted answers/errors | **No** |
| Gemini (free plan) | Fake function | **No** |
| Database | Throw-away SQLite file for the test suite; **also** a manual run of the migration and the core payment/queue/cost flow on a real **PostgreSQL 16** (fresh database, and an old-shape database with data) | Local test DB only |

**Not tested, and cannot be from here:** a real Razorpay Live payment, a real Razorpay sandbox checkout, real webhooks delivered by Razorpay, real OpenAI/Gemini calls and the real model response format of `gpt-6-luna` / `gpt-6.1-sol` (the request shape was kept from your existing working code; both model IDs were confirmed to exist in OpenAI's published model docs, and prices were taken from those pages), Supabase itself, Render, email sending, and browser behaviour of the Razorpay Checkout pop-up. **No real money was charged and no real payment processing was verified.** Do the first-payment check in README section D(6) after you add your Live keys.

## Your 20 requested checks
1. Free Gemini generation - `test_generation::test_basic_plan_uses_gemini_and_never_openai` (any OpenAI client creation would fail the test)
2. Personalized order - `test_payments::test_personalized_order_uses_server_price` (Rs 5 / 500 paise even if the browser sends another amount)
3. Advanced order - `test_advanced_order_uses_server_price` (1500 paise)
4. Wrong amount / currency - `test_wrong_amount_rejected`, `test_wrong_currency_rejected`, `test_webhook_wrong_amount_never_unlocks`
5. Invalid signature - `test_invalid_signature_rejected`, webhook signature tests
6. Duplicate callback - `test_duplicate_callback_makes_one_plan`, `test_same_payment_id_cannot_pay_second_order`
7. Duplicate webhook - `test_duplicate_webhook_delivery_is_ignored`, `test_captured_and_order_paid_events_make_one_plan`
8. Cancellation - `test_cancel_marks_cancelled_and_late_payment_still_counts`
9. Failure - `test_payment_failed_callback`, `test_payment_failed_webhook_then_success_on_same_order`
10. Successful payment - `test_successful_payment_verified_and_plan_queued` (also: webhook-before-callback, callback-before-webhook, authorized->captured, pending payment does not unlock)
11. Successful OpenAI generation - `test_personalized_generation_records_tokens_and_exact_cost`, `test_advanced_uses_advanced_model_and_coach_notes`
12. Invalid JSON - `test_invalid_json_two_attempts_both_billed`, `test_repair_attempt_can_succeed`
13. Timeout - `test_timeout_records_unknown_cost_and_stops`, `test_sdk_hidden_retries_are_disabled`
14. Retry - `test_retry_after_failure_reuses_payment` (no new order, no new charge), `test_retries_are_capped`
15. Failed-generation accounting - every attempt is a row; totals include rejected answers
16. Token tracking - input/output/cached/reasoning/request id stored per attempt
17. Cost calculation - `test_cost_maths` (Decimal, unknown model = unknown not zero, long-context = unknown), pricing override
18. Admin dashboard - `test_admin_financial_dashboard_numbers`, `test_admin_filters`, `test_unknown_costs_are_flagged_not_zero`
19. Unauthorized admin - `test_admin_pages_require_admin` (anonymous, normal user, refund endpoint)
20. Production rejects test keys - `test_production_rejects_test_keys`, `test_production_order_creation_refused_with_test_key`, `test_production_startup_rejects_default_secret_key`, `test_production_cookies_secure_and_payments_off_with_test_key`

Also covered: spending limits (daily, per generation, attempt cap), duplicate job runners (atomic claim), stale/lost jobs become retryable, refunds only by explicit admin action and only once, refund webhook, CSRF on payment routes (webhook exempt, signature-protected), rate limiting, migration of an old database without data loss and running twice safely, production site pauses until migrated, no secrets in templates/JS, `.env.example` has placeholders only, model/provider names never appear on customer pages.

## Static checks
`pyflakes` clean (one pre-existing unused variable in an old route), all Python compiles, all 31 templates parse, route registration and the URLs used by `plans.js` verified by test.

## All test cases
- test_admin_config.py::test_admin_pages_require_admin
- test_admin_config.py::test_admin_financial_dashboard_numbers
- test_admin_config.py::test_admin_filters
- test_admin_config.py::test_unknown_costs_are_flagged_not_zero
- test_admin_config.py::test_cost_maths
- test_admin_config.py::test_pricing_override_from_environment
- test_admin_config.py::test_production_rejects_test_keys
- test_admin_config.py::test_production_requires_live_key_secret_and_webhook
- test_admin_config.py::test_development_allows_test_keys_but_never_silent_fallback
- test_admin_config.py::test_production_order_creation_refused_with_test_key
- test_admin_config.py::test_production_webhook_and_verify_blocked_with_test_key
- test_admin_config.py::test_production_startup_rejects_default_secret_key
- test_admin_config.py::test_production_cookies_secure_and_payments_off_with_test_key
- test_admin_config.py::test_production_blocks_site_until_migrated
- test_admin_config.py::test_env_example_has_no_real_secrets
- test_admin_config.py::test_no_secret_in_frontend_files
- test_admin_config.py::test_healthz
- test_admin_config.py::test_rate_limiter_on_payment_endpoints
- test_generation.py::test_basic_plan_uses_gemini_and_never_openai
- test_generation.py::test_basic_plan_failure_is_retryable_and_free
- test_generation.py::test_personalized_generation_records_tokens_and_exact_cost
- test_generation.py::test_advanced_uses_advanced_model_and_coach_notes
- test_generation.py::test_models_come_from_environment
- test_generation.py::test_invalid_json_two_attempts_both_billed
- test_generation.py::test_repair_attempt_can_succeed
- test_generation.py::test_attempt_limit_is_configurable
- test_generation.py::test_timeout_records_unknown_cost_and_stops
- test_generation.py::test_sdk_hidden_retries_are_disabled
- test_generation.py::test_retry_after_failure_reuses_payment
- test_generation.py::test_retries_are_capped
- test_generation.py::test_paid_plan_never_generated_without_verified_payment
- test_generation.py::test_daily_limit_blocks_openai_call
- test_generation.py::test_per_generation_limit_stops_repairs
- test_generation.py::test_duplicate_runners_cannot_double_generate
- test_generation.py::test_stale_running_job_becomes_retryable
- test_generation.py::test_users_never_see_model_or_provider_names
- test_migrations.py::test_old_database_upgrades_without_losing_data
- test_migrations.py::test_postgres_migration_refuses_without_backup_confirmation
- test_pages.py::test_frontend_endpoints_exist
- test_pages.py::test_home_and_profile_pages_render_in_every_state
- test_pages.py::test_failed_state_shows_retry_and_refund
- test_pages.py::test_plan_ui_states_present_in_markup
- test_payments.py::test_personalized_order_uses_server_price
- test_payments.py::test_advanced_order_uses_server_price
- test_payments.py::test_unknown_tier_and_basic_rejected
- test_payments.py::test_order_requires_login
- test_payments.py::test_double_click_reuses_open_order
- test_payments.py::test_successful_payment_verified_and_plan_queued
- test_payments.py::test_authorized_payment_is_captured_first
- test_payments.py::test_invalid_signature_rejected
- test_payments.py::test_wrong_amount_rejected
- test_payments.py::test_wrong_currency_rejected
- test_payments.py::test_other_users_order_cannot_be_verified
- test_payments.py::test_pending_payment_does_not_unlock
- test_payments.py::test_duplicate_callback_makes_one_plan
- test_payments.py::test_same_payment_id_cannot_pay_second_order
- test_payments.py::test_cancel_marks_cancelled_and_late_payment_still_counts
- test_payments.py::test_payment_failed_callback
- test_payments.py::test_payment_failed_webhook_then_success_on_same_order
- test_payments.py::test_webhook_bad_signature_rejected
- test_payments.py::test_webhook_signature_uses_webhook_secret_not_key_secret
- test_payments.py::test_webhook_before_callback_then_callback
- test_payments.py::test_callback_before_webhook
- test_payments.py::test_duplicate_webhook_delivery_is_ignored
- test_payments.py::test_captured_and_order_paid_events_make_one_plan
- test_payments.py::test_webhook_wrong_amount_never_unlocks
- test_payments.py::test_webhook_unknown_order_is_ok
- test_payments.py::test_webhook_processing_error_returns_500_so_razorpay_retries
- test_payments.py::test_webhook_needs_no_csrf_but_other_posts_do
- test_payments.py::test_refund_flow_is_manual_and_single_shot
- test_payments.py::test_refund_webhook_updates_record
- test_payments.py::test_refund_request_refused_when_plan_delivered
