"""FastAPI application entry point.

Purpose: assembles the application (logging, CORS, error handlers, routers). The business routers
(inventory, sales, replenishment, events) are added in later tasks.

Relations: run with `uvicorn app.main:app`; tests use `create_app()` so that
each test gets a new instance with its own dependency overrides.
"""
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.db import SessionLocal
from app.errors import register_error_handlers
from app.logging_setup import configure_logging
from app.odoo.factory import get_odoo
from app.routers import auth, events, health, inventory, odoo_config, odoo_connection, replenishment, sales
from app.services.connection import get_monitor, startup_tasks


@asynccontextmanager
async def lifespan(_: FastAPI):
    # At server start: check the Odoo connection, then (if connected) populate local tables that are still empty.
    # Runs in a background thread so the API serves requests immediately even if Odoo is slow or down;
    # a failure only becomes the connection status and never fails the startup.
    threading.Thread(
        target=startup_tasks, args=(SessionLocal, get_odoo, get_monitor()), daemon=True, name="odoo-startup"
    ).start()
    yield


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="Verve Inventory API", lifespan=lifespan)

    # CORS: only frontend origins registered in CORS_ORIGINS may call the API from a browser.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().cors_origin_list,
        allow_credentials=True,  # the admin session cookie is sent cross-origin (frontend :3000 to API :8000)
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(inventory.router)
    app.include_router(sales.router)
    app.include_router(events.router)
    app.include_router(odoo_connection.router)
    app.include_router(odoo_config.router)
    app.include_router(replenishment.router)
    return app


app = create_app()
