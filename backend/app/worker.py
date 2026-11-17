"""Outbox worker process: `python -m app.worker`.

Purpose: runs services/outbox.process_next repeatedly as a process separate from the API,
so that sales do not wait for Odoo and more than one worker can be run (SKIP LOCKED).

Relations: uses SessionLocal (app/db.py), get_odoo (app/odoo/factory.py), services/outbox.py.
Unexpected errors are only logged; the worker stays alive and tries again in the next cycle.
"""
import logging
import time
from datetime import datetime, timezone

from app.db import SessionLocal
from app.errors import AppError
from app.logging_setup import configure_logging
from app.odoo.factory import get_odoo
from app.services.outbox import process_next

log = logging.getLogger("app.worker")


def run_forever(poll_seconds: float = 2.0) -> None:
    configure_logging()
    log.info("worker.start")
    while True:
        processed = 0
        try:
            client = get_odoo()  # cheap: reused unless the Odoo configuration changes (Configuration menu)
            with SessionLocal() as session:
                while process_next(session, client, datetime.now(timezone.utc)):
                    processed += 1
        except AppError as exc:
            if exc.code != "ODOO_NOT_CONFIGURED":
                log.exception("worker.error")
        except Exception:
            log.exception("worker.error")
        if processed == 0:
            time.sleep(poll_seconds)


if __name__ == "__main__":
    run_forever()
