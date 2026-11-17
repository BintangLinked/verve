"""Tests for the outbox worker (design sections 8.4 and 8.5).

Purpose: to prove that event delivery to Odoo (mock) is correct: success marks DONE and
SYNCED, a transient fault is retried with backoff and then FAILED after the limit,
a business rejection is FAILED immediately, a re-sent event does NOT deduct Odoo stock twice,
stuck events are recovered, and two workers do not claim the same event.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.schemas.sales import SaleRequest
from app.services.outbox import claim_next, process_next
from app.services.sales import record_sale


def now():
    return datetime.now(timezone.utc)


def make_sale(session, vid, request_id="req-1", quantity=1):
    """POS sale at SHOP1 for TS-BLK-L; also writes a SALE event with PENDING status."""
    cmd = SaleRequest(request_id=request_id, variant_id=vid("TS-BLK-L"), quantity=quantity, channel="POS", store_id="SHOP1")
    record_sale(session, cmd)


def event(session, key="SALE:req-1"):
    return session.execute(
        text("select status, attempts, next_attempt_at, last_error from integration_events where idempotency_key = :k"),
        {"k": key},
    ).one()


def sale_status(session, key="req-1"):
    return session.execute(text("select odoo_status from sales where request_id = :k"), {"k": key}).scalar_one()


def odoo_qty(odoo, odoo_variant_id, location):
    return next(s.qty_sellable for s in odoo.get_stock() if s.odoo_variant_id == odoo_variant_id and s.location_code == location)


def test_success_marks_event_done_and_sale_synced(session, seeded, vid, odoo):
    make_sale(session, vid)
    assert process_next(session, odoo, now()) is True
    assert event(session).status == "DONE"
    assert sale_status(session) == "SYNCED"
    assert odoo_qty(odoo, 101, "SHOP1") == 7
    assert process_next(session, odoo, now()) is False  # none are due any more


def test_transient_failure_backs_off_then_succeeds(session, seeded, vid, odoo):
    make_sale(session, vid)
    t0 = now()
    odoo.fail_next("transient", 1)

    assert process_next(session, odoo, t0) is True
    ev = event(session)
    assert (ev.status, ev.attempts) == ("PENDING", 1)
    assert ev.next_attempt_at > t0  # backoff: not yet due

    assert process_next(session, odoo, t0) is False  # still waiting for backoff
    assert process_next(session, odoo, t0 + timedelta(minutes=5)) is True
    assert event(session).status == "DONE"


def test_transient_exhausts_to_failed(session, seeded, vid, odoo):
    make_sale(session, vid)
    odoo.fail_next("transient", 5)
    t = now()
    for _ in range(5):
        assert process_next(session, odoo, t) is True
        t += timedelta(hours=1)  # always past the backoff
    ev = event(session)
    assert (ev.status, ev.attempts) == ("FAILED", 5)
    assert "transient" in ev.last_error
    assert sale_status(session) == "FAILED"


class ExplodingClient:
    """Odoo client that raises an unexpected error (bug, corrupt payload) instead of an Odoo error."""

    def post_sale(self, *args, **kwargs):
        raise KeyError("boom customer-data-123")  # this text must not be stored


def test_unexpected_exception_counts_as_attempt_and_eventually_fails(session, seeded, vid):
    make_sale(session, vid)
    t = now()
    assert process_next(session, ExplodingClient(), t) is True
    ev = event(session)
    assert (ev.status, ev.attempts) == ("PENDING", 1)  # does not remain stuck in PROCESSING
    assert ev.last_error == "KeyError"  # only the error type; the free text (which may contain Odoo data) is not stored
    assert "customer-data-123" not in ev.last_error
    for _ in range(4):
        t += timedelta(hours=1)
        assert process_next(session, ExplodingClient(), t) is True
    ev = event(session)
    assert (ev.status, ev.attempts) == ("FAILED", 5)
    assert sale_status(session) == "FAILED"


def test_business_error_fails_immediately(session, seeded, vid, odoo):
    make_sale(session, vid)
    odoo.fail_next("business", 1)
    assert process_next(session, odoo, now()) is True
    ev = event(session)
    assert (ev.status, ev.attempts) == ("FAILED", 1)
    assert process_next(session, odoo, now() + timedelta(hours=1)) is False  # FAILED is not retried automatically


def test_duplicate_event_does_not_double_deduct(session, seeded, vid, odoo):
    make_sale(session, vid)
    process_next(session, odoo, now())
    assert odoo_qty(odoo, 101, "SHOP1") == 7
    # Simulated crash after Odoo accepted but before the event was marked DONE: the event returns to PENDING.
    session.execute(text("update integration_events set status = 'PENDING' where idempotency_key = 'SALE:req-1'"))
    session.commit()
    assert process_next(session, odoo, now()) is True
    assert odoo_qty(odoo, 101, "SHOP1") == 7  # Odoo recognises the same reference
    assert event(session).status == "DONE"


def test_stale_processing_event_requeued(session, seeded, vid, odoo):
    make_sale(session, vid)
    t = now()
    session.execute(
        text("update integration_events set status = 'PROCESSING', next_attempt_at = :t"),
        {"t": t - timedelta(minutes=10)},
    )
    session.commit()
    assert process_next(session, odoo, t) is True
    assert event(session).status == "DONE"


def test_fresh_processing_event_left_alone(session, seeded, vid, odoo):
    make_sale(session, vid)
    t = now()
    session.execute(
        text("update integration_events set status = 'PROCESSING', next_attempt_at = :t"),
        {"t": t - timedelta(minutes=1)},
    )
    session.commit()
    assert process_next(session, odoo, t) is False
    assert event(session).status == "PROCESSING"


def test_two_workers_do_not_claim_same_event(session, seeded, vid, session_factory):
    make_sale(session, vid)
    with session_factory() as worker_a, session_factory() as worker_b:
        # Worker A holds the event row lock (as if claiming it).
        worker_a.execute(text("select id from integration_events where status = 'PENDING' for update"))
        assert claim_next(worker_b, now()) is None  # SKIP LOCKED: worker B does not wait and does not claim it
        worker_a.rollback()
        claimed = claim_next(worker_b, now())
        assert claimed is not None and claimed.idempotency_key == "SALE:req-1"


def test_replenishment_event_submitted(session, seeded, vid, odoo):
    variant_id = vid("TS-WHT-L")
    store_id = session.execute(text("select id from locations where code = 'SHOP2'")).scalar_one()
    request_pk = session.execute(
        text(
            "insert into replenishment_requests (request_id, payload_hash, store_location_id, variant_id, qty_requested) "
            "values ('rep-1', 'h', :s, :v, 5) returning id"
        ),
        {"s": store_id, "v": variant_id},
    ).scalar_one()
    session.execute(
        text(
            "insert into integration_events (event_type, aggregate_id, idempotency_key, payload) values "
            "('REPLENISHMENT', :a, 'rep-1', CAST(:p AS JSONB))"
        ),
        {"a": request_pk, "p": '{"odoo_variant_id": 103, "from_code": "WH", "to_code": "SHOP2", "qty": 5}'},
    )
    session.commit()

    assert process_next(session, odoo, now()) is True
    status = session.execute(text("select status from replenishment_requests where request_id = 'rep-1'")).scalar_one()
    assert status == "SUBMITTED"
    assert odoo_qty(odoo, 103, "SHOP2") == 9
    assert odoo_qty(odoo, 103, "WH") == 15
