"""Logging setup: one line per event in key=value format.

Purpose: logs are easy to search (grep request_id=...) when tracing duplicate sales or
failed Odoo events. Keys such as request_id and odoo_ref are inserted by the caller
through the log message, e.g. log.info("sale.created request_id=%s", rid).

Relations: called once from app/main.py (API) and app/worker.py (worker).
"""
import logging


def configure_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="ts=%(asctime)s level=%(levelname)s logger=%(name)s msg=%(message)s",
        force=True,  # override the default uvicorn/pytest configuration so the format is consistent
    )
