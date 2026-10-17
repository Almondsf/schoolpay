"""The transactional outbox (app/core/outbox.py)."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.core import outbox
from app.core.outbox import OutboxStatus, OutboxTask


@pytest.fixture
def sent(monkeypatch):
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(outbox, "_send", lambda name, payload, task_id: calls.append((name, payload)))
    return calls


def status_of(db, row_id):
    db.expire_all()
    return db.get(OutboxTask, row_id)


def test_committed_task_is_dispatched_straight_away(db, sent):
    row = outbox.enqueue(db, "system.ping", {})
    db.commit()
    assert sent == [("system.ping", {})]
    assert status_of(db, row.id).status == OutboxStatus.DISPATCHED


def test_rolled_back_task_is_never_sent(db, sent):
    outbox.enqueue(db, "system.ping", {})
    db.rollback()
    db.commit()
    assert sent == []
    assert db.scalars(select(OutboxTask)).all() == []


def test_failed_hand_off_stays_pending_with_backoff_then_sweeper_sends_it(db, monkeypatch):
    def broker_down(*_args):
        raise ConnectionError("redis unreachable")

    monkeypatch.setattr(outbox, "_send", broker_down)
    row = outbox.enqueue(db, "system.ping", {"n": 1})
    db.commit()
    pending = status_of(db, row.id)
    assert pending.status == OutboxStatus.PENDING and pending.attempts == 1
    assert "ConnectionError" in pending.last_error
    assert pending.next_attempt_at > datetime.now(UTC)

    sent: list[str] = []
    monkeypatch.setattr(outbox, "_send", lambda name, payload, task_id: sent.append(name))
    assert outbox.sweep() == 0  # not due yet
    db.execute(update(OutboxTask).values(next_attempt_at=datetime.now(UTC) - timedelta(seconds=1)))
    db.commit()
    assert outbox.sweep() == 1
    assert sent == ["system.ping"]
    assert status_of(db, row.id).status == OutboxStatus.DISPATCHED


def test_task_is_dead_lettered_after_max_attempts_and_can_be_requeued(db, monkeypatch):
    def broker_down(*_args):
        raise ConnectionError("down")

    monkeypatch.setattr(outbox, "_send", broker_down)
    row = outbox.enqueue(db, "system.ping", {})
    db.commit()
    for _ in range(outbox.MAX_ATTEMPTS):
        db.execute(update(OutboxTask).values(next_attempt_at=datetime.now(UTC) - timedelta(seconds=1)))
        db.commit()
        outbox.sweep()
    assert status_of(db, row.id).status == OutboxStatus.FAILED
    assert outbox.requeue_failed() == 1
    assert status_of(db, row.id).status == OutboxStatus.PENDING


def test_dispatch_claims_all_rows_in_one_query(db, sent, connection):
    """Many tasks committed together are handed off without a query per row."""
    from sqlalchemy import event

    statements: list[str] = []

    def count(_conn, _cursor, statement, *_args):
        if "outbox_tasks" in statement and statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(connection, "before_cursor_execute", count)
    try:
        for i in range(5):
            outbox.enqueue(db, "system.ping", {"i": i})
        db.commit()
    finally:
        event.remove(connection, "before_cursor_execute", count)
    assert len(sent) == 5
    assert len(statements) == 1
