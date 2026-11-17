"""Two kinds of Odoo integration error, each carrying a safe machine CODE.

Purpose: the outbox worker (app/services/outbox.py) treats the two differently:
- OdooTransientError : temporary disruption or a correctable misconfiguration -> retried.
- OdooBusinessError  : business rejection (insufficient Odoo stock, unknown product) -> goes straight to FAILED,
                       there is no point retrying without operator intervention.
`code` is used by the connection check (odoo/live.py) to build a status that is safe to display:
UNREACHABLE, TIMEOUT, URL_NOT_ALLOWED, DATABASE_NOT_FOUND, AUTH_FAILED, and so on.
Error messages must not contain secrets; the live client sanitises them before raising.

Relations: raised by mock.py and live.py, caught by the outbox worker and the sync service.
"""


class OdooTransientError(Exception):
    """Temporary disruption; safe to retry."""

    def __init__(self, message: str = "", code: str = "UNREACHABLE"):
        super().__init__(message)
        self.code = code


class OdooBusinessError(Exception):
    """Odoo rejected the operation; retrying with the same data will not succeed."""

    def __init__(self, message: str = "", code: str = "ODOO_REJECTED"):
        super().__init__(message)
        self.code = code
