"""Tests for PostgreSQL schema constraints (design section 6.1).

Purpose: to prove that the database itself rejects data that violates business rules
(negative stock, duplicate request_id, qty <= 0), so the rules do not depend solely on application code.
"""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


def _insert_stock(session, ids, sellable=0, damaged=0):
    session.execute(
        text(
            "insert into stock_levels (variant_id, location_id, qty_sellable, qty_damaged) "
            "values (:v, :l, :s, :d)"
        ),
        {"v": ids["variant_id"], "l": ids["location_id"], "s": sellable, "d": damaged},
    )


def _insert_sale(session, ids, request_id="req-1", qty=1):
    session.execute(
        text(
            "insert into sales (request_id, payload_hash, variant_id, location_id, qty, channel) "
            "values (:r, 'h', :v, :l, :q, 'POS')"
        ),
        {"r": request_id, "v": ids["variant_id"], "l": ids["location_id"], "q": qty},
    )


def _insert_event(session, key="k-1"):
    session.execute(
        text(
            "insert into integration_events (event_type, aggregate_id, idempotency_key, payload) "
            "values ('SALE', 1, :k, '{}'::jsonb)"
        ),
        {"k": key},
    )


def test_negative_sellable_rejected(session, basic_ids):
    with pytest.raises(IntegrityError):
        _insert_stock(session, basic_ids, sellable=-1)
        session.commit()


def test_negative_damaged_rejected(session, basic_ids):
    with pytest.raises(IntegrityError):
        _insert_stock(session, basic_ids, damaged=-1)
        session.commit()


def test_sale_request_id_unique(session, basic_ids):
    _insert_sale(session, basic_ids, "same-key")
    session.commit()
    with pytest.raises(IntegrityError):
        _insert_sale(session, basic_ids, "same-key")
        session.commit()


def test_integration_event_key_unique(session):
    _insert_event(session, "same-key")
    session.commit()
    with pytest.raises(IntegrityError):
        _insert_event(session, "same-key")
        session.commit()


def test_sale_quantity_must_be_positive(session, basic_ids):
    with pytest.raises(IntegrityError):
        _insert_sale(session, basic_ids, qty=0)
        session.commit()
