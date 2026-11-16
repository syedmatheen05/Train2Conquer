"""Background worker for paid/free plan generation.

Run it as a second process next to the website:      python worker.py
It looks in the database for queued plan jobs, claims each one (so two
workers can never do the same job) and runs it. If it is stopped or crashes,
unfinished jobs are marked failed after a while and the customer can retry
without paying again.

Use it together with GENERATION_MODE=worker on the website.
"""
import os
import signal
import time

import main   # loads settings, database and the generation code

POLL_SECONDS = max(1, int(os.environ.get("WORKER_POLL_SECONDS", "3")))
_running = True


def _stop(*_):
    global _running
    _running = False


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    print("Train2Conquer worker started (%s)" % main.WORKER_ID)
    while _running:
        try:
            handled = main.process_queue(limit=5)
        except Exception as exc:     # keep the worker alive; the error is logged
            print("WORKER ERROR:", type(exc).__name__)
            handled = 0
        if not handled:
            time.sleep(POLL_SECONDS)
    print("Train2Conquer worker stopped")
