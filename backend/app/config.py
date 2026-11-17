"""Application configuration, read from environment variables or the backend/.env file.

Purpose: a single place for all settings (database, Odoo, CORS) so that no value is
hard-coded. Odoo credentials exist only in this backend and are NEVER sent
to the frontend (see design section 8.2).

Relations: used by app/db.py (connection), app/odoo/factory.py (mock/live selection),
app/main.py (CORS), and tests/conftest.py (test database).
"""
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# The location of backend/.env is computed from the position of this file, not from the folder the command is run from.
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    # Application database (database_verve) and the test-only database (emptied for each test).
    database_url: str
    test_database_url: Optional[str] = None

    # Odoo integration: "mock" (default) or "live". Odoo credentials (URL, database, API key) are NOT read from
    # the environment: everything is managed through the Odoo configuration menu and stored encrypted in the database.
    # Without a stored configuration, "live" mode means not yet connected; "mock" mode uses sample data.
    odoo_mode: Literal["mock", "live"] = "mock"

    # SSRF policy for the stored Odoo URL: private/loopback addresses are blocked unless explicitly permitted
    # (set to true when Odoo runs on localhost/an internal network). Link-local and cloud metadata addresses are
    # ALWAYS blocked.
    odoo_allow_private_hosts: bool = False

    # Application authentication: admin login for the Odoo connection endpoints. All values come from the
    # backend environment; if any is empty, authentication is treated as not configured and fails closed.
    admin_username: Optional[str] = None
    admin_password_hash: Optional[str] = None  # output of `python -m app.hash_password`
    session_secret: Optional[SecretStr] = None  # session cookie signing secret, at least 32 characters
    session_ttl_minutes: int = 60
    cookie_secure: bool = False  # set to true when the API is served over HTTPS

    # Encryption key (Fernet) for the Odoo API key stored through the Configuration menu. Generate with
    # `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
    # Empty = storing the API key through the application is rejected (503).
    settings_encryption_key: Optional[SecretStr] = None

    # List of frontend origins permitted to call the API, comma-separated.
    cors_origins: str = "http://localhost:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    """Settings are created once and then reused throughout the application."""
    return Settings()
