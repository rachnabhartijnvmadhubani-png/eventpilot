"""Deliver EventPilot's durable email queue without blocking HTTP requests."""
import argparse
import logging
import signal
import threading
from urllib.request import urlopen

import main
from email_service import is_configured

log = logging.getLogger("eventpilot.email_worker")
stop = threading.Event()


def deliver_batch(limit):
    totals = {"claimed": 0, "sent": 0, "failed": 0}
    for _ in range(limit):
        if stop.is_set():
            break
        result = main.process_email_outbox(limit=1)
        for key in totals:
            totals[key] += int(result.get(key, 0))
        if not result.get("claimed"):
            break
    if totals["claimed"]:
        # Neither recipients, message bodies nor credentials belong in logs.
        log.info("Email batch: %s sent, %s failed", totals["sent"], totals["failed"])
    return totals


def ready():
    try:
        with urlopen("http://127.0.0.1:8001/api/health", timeout=2) as response:
            return response.status == 200
    except Exception:
        return False


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    if not 1 <= args.limit <= 100:
        parser.error("Limit must be between 1 and 100.")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.once:
        if not is_configured():
            log.info("SMTP is not configured; queued notifications are retained.")
            return
        deliver_batch(args.limit)
        return
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    waiting_logged = False
    while not stop.is_set():
        try:
            if not is_configured():
                if not waiting_logged:
                    log.info("SMTP is not configured; queued notifications are retained.")
                    waiting_logged = True
                stop.wait(15)
                continue
            if not ready():
                stop.wait(5)
                continue
            waiting_logged = False
            totals = deliver_batch(args.limit)
            stop.wait(1 if totals["claimed"] >= args.limit else 5)
        except Exception as error:
            log.error("Email worker error (%s); will retry.", type(error).__name__)
            stop.wait(15)
    log.info("Email worker stopped between deliveries.")


if __name__ == "__main__":
    run()
