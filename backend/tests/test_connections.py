"""Connections are reused and concurrent billing is serialised: shared HTTP clients and advisory locks."""

import uuid

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import db as db_module
from app.core import locks, outbound


def test_one_http_client_per_provider_is_reused_and_each_request_carries_its_own_headers(monkeypatch):
    seen: list[httpx.Request] = []
    outbound.close_all()
    real = httpx.Client

    def client_with_mock_transport(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(lambda r: (seen.append(r), httpx.Response(200, json={}))[1])
        return real(*args, **kwargs)

    monkeypatch.setattr(outbound.httpx, "Client", client_with_mock_transport)
    try:
        with outbound.outbound_client(
            "paystack", base_url="https://api.example", headers={"Authorization": "a"}
        ) as one:
            one.get("/first")
        with outbound.outbound_client(
            "paystack", base_url="https://api.example", headers={"Authorization": "b"}
        ) as two:
            two.post("/second", headers={"X-Extra": "1"})
        assert one._client is two._client  # the same pool: no new connection per call
        assert [r.headers["authorization"] for r in seen] == ["a", "b"]
        assert seen[1].headers["x-extra"] == "1"
        with outbound.outbound_client("termii", base_url="https://api.example") as other:
            assert other._client is not one._client  # each provider has its own
        assert len(outbound._clients) == 2  # keys never include secrets, so the cache can't grow per request
    finally:
        outbound.close_all()
    assert outbound._clients == {}


def test_a_forked_process_starts_without_the_parents_clients():
    with outbound.outbound_client("paystack", base_url="https://api.example"):
        pass
    assert outbound._clients
    outbound._forget_after_fork()  # what os.register_at_fork runs in the child
    assert outbound._clients == {}


def test_the_billing_lock_keeps_a_second_connection_out_until_the_first_ends(db: Session):
    school = uuid.uuid4()
    locks.hold(db, "billing", school)
    key = func.hashtextextended(f"billing:{school}", 0)
    with db_module.engine.connect() as other:  # a second, independent connection (another request)
        assert other.execute(select(func.pg_try_advisory_xact_lock(key))).scalar() is False
        # Another school's billing isn't held up
        free = func.hashtextextended(f"billing:{uuid.uuid4()}", 0)
        assert other.execute(select(func.pg_try_advisory_xact_lock(free))).scalar() is True
        other.rollback()
