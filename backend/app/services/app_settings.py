"""Application settings stored in PostgreSQL (app_settings table).

Purpose: the Odoo configuration and the admin password hash can be changed through the application without editing `.env`.
The Odoo API key is stored ENCRYPTED (Fernet) and is decrypted only in the backend when building the Odoo client;
the original value is never sent to the browser or written to the log.

Relations: used by routers/odoo_config.py (save/read), routers/auth.py (change password), app/auth.py
(effective hash), and app/odoo/factory.py (builds the client from the stored configuration).
"""
import hashlib
from typing import NamedTuple, Optional

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import AppError

ODOO_KEYS = ("odoo.url", "odoo.database", "odoo.api_key")
IDENTITY_KEY = "odoo_identity"  # fingerprint of the Odoo server+database last connected; survives Disconnect
ADMIN_HASH_KEY = "admin.password_hash"


class OdooConfig(NamedTuple):
    url: str
    database: str
    api_key: str  # already decrypted; do not log or return to the API


def _get(session: Session, key: str) -> Optional[str]:
    return session.execute(text("select value from app_settings where key = :k"), {"k": key}).scalar_one_or_none()


def _put(session: Session, key: str, value: str) -> None:
    session.execute(
        text(
            "insert into app_settings (key, value) values (:k, :v) "
            "on conflict (key) do update set value = excluded.value, updated_at = clock_timestamp()"
        ),
        {"k": key, "v": value},
    )


def _fernet(settings: Settings) -> Fernet:
    key = settings.settings_encryption_key
    if not key:
        raise AppError("ENCRYPTION_NOT_CONFIGURED", "SETTINGS_ENCRYPTION_KEY is not configured on the server", 503)
    return Fernet(key.get_secret_value().encode())


def fingerprint(session: Session) -> str:
    """Changes every time the Odoo configuration is saved; used by the API and the worker to rebuild the client."""
    return session.execute(text("select coalesce(max(updated_at)::text, '') from app_settings where key like 'odoo.%'")).scalar_one()


def load_odoo_config(session: Session, settings: Settings) -> Optional[OdooConfig]:
    """Stored configuration, or None if incomplete."""
    values = [_get(session, k) for k in ODOO_KEYS]
    if not all(values):
        return None
    url, database, token = values
    try:
        api_key = _fernet(settings).decrypt(token.encode()).decode()
    except InvalidToken:
        # The encryption key was changed: the old API key is unreadable. Treat as not configured, do not crash.
        return None
    return OdooConfig(url, database, api_key)


def stored_summary(session: Session) -> Optional[dict]:
    """URL and database, and whether an API key exists, WITHOUT the API key. None if not yet saved."""
    url, database, token = (_get(session, k) for k in ODOO_KEYS)
    if not (url and database and token):
        return None
    return {"url": url, "database": database}


def identity(url: str, database: str) -> str:
    """Fingerprint (server, database) of Odoo. Used to recognise a move to another Odoo project."""
    return hashlib.sha256(f"{url.strip().rstrip('/').lower()}|{database.strip()}".encode()).hexdigest()


def is_other_odoo(session: Session, url: str, database: str) -> bool:
    """True if the application has ever been connected to an Odoo/database DIFFERENT from the one requested."""
    previous = _get(session, IDENTITY_KEY)
    return previous is not None and previous != identity(url, database)


def save_odoo_config(session: Session, settings: Settings, url: str, database: str, api_key: Optional[str]) -> None:
    """Save the configuration (the caller performs the commit). Empty api_key = keep the stored one, only
    if the server and database do not change; the old key is never sent to another server/database."""
    fernet = _fernet(settings)
    if api_key:
        token = fernet.encrypt(api_key.encode()).decode()
    else:
        previous = _get(session, IDENTITY_KEY)
        token = _get(session, "odoo.api_key") if previous == identity(url, database) else None
        if not token:
            raise AppError("API_KEY_REQUIRED", "An API key is required for a new connection or when the URL or database changes", 422)
    for key, value in (("odoo.url", url), ("odoo.database", database), ("odoo.api_key", token), (IDENTITY_KEY, identity(url, database))):
        _put(session, key, value)


def clear_odoo_config(session: Session) -> None:
    """Disconnect: delete the stored URL, database, and API key. The fingerprint remains, so a project change is detected."""
    # Includes 'odoo.login' from the older version (the login column has been removed).
    session.execute(text("delete from app_settings where key = any(:keys)"), {"keys": [*ODOO_KEYS, "odoo.login"]})


# Tables belonging to one Odoo project (Odoo ids are mapped within them). Order is not important: TRUNCATE ... CASCADE.
LOCAL_TABLES = ("integration_events", "replenishment_requests", "sales", "stock_movements", "stock_levels", "variants", "products", "locations")


def local_data_counts(session: Session) -> dict:
    """Number of local business data rows; 'unsent_events' = events to Odoo not yet sent."""
    counts = {t: session.execute(text(f"select count(*) from {t}")).scalar_one() for t in ("locations", "variants", "sales")}
    counts["unsent_events"] = session.execute(text("select count(*) from integration_events where status <> 'DONE'")).scalar_one()
    return counts


def reset_local_data(session: Session) -> None:
    """Delete local business data (catalogue, stock, sales, events). Used when moving to another Odoo project."""
    session.execute(text("truncate " + ", ".join(LOCAL_TABLES) + " restart identity cascade"))


def get_admin_password_hash(session: Session) -> Optional[str]:
    return _get(session, ADMIN_HASH_KEY)


def set_admin_password_hash(session: Session, password_hash: str) -> None:
    _put(session, ADMIN_HASH_KEY, password_hash)
    session.commit()
