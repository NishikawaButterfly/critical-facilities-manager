from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from cfm.models import ApiToken, User
from cfm.ratelimit import AuthRateLimiter
from cfm.services.tokens import hash_token

from .conftest import ACTOR, ADMIN, VIEWER, build_app, create_hierarchy

pytestmark = pytest.mark.anyio


def auth_header_for_secret(secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}"}


def _install_token(
    app: FastAPI,
    username: str,
    secret: str,
    *,
    expires_at: datetime | None = None,
    revoked: bool = False,
) -> None:
    """Plant a token row directly, bypassing the API, for 401-path tests."""
    with app.state.session_factory() as session:
        user_id = session.scalar(select(User.id).where(User.username == username))
        assert user_id is not None
        session.add(
            ApiToken(
                user_id=user_id,
                label="planted",
                token_hash=hash_token(secret),
                expires_at=expires_at,
                revoked=revoked,
            )
        )
        session.commit()


async def test_me_returns_the_authenticated_identity(client: AsyncClient) -> None:
    for headers, username, role in [
        (ADMIN, "test-admin", "admin"),
        (ACTOR, "test-engineer", "engineer"),
        (VIEWER, "test-viewer", "viewer"),
    ]:
        response = await client.get("/api/v1/me", headers=headers)
        assert response.status_code == 200
        payload = response.json()
        assert payload["username"] == username
        assert payload["role"] == role
        assert payload["is_active"] is True
        assert "token" not in payload


async def test_me_requires_authentication(anon_client: AsyncClient) -> None:
    response = await anon_client.get("/api/v1/me")
    assert response.status_code == 401
    assert response.json()["error_code"] == "auth.credentials_required"


async def test_unknown_token_rejected(client: AsyncClient) -> None:
    response = await client.get(
        "/api/v1/locations", headers={"Authorization": "Bearer no-such-secret"}
    )
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error_code"] == "auth.invalid_token"


async def test_wrong_scheme_rejected(client: AsyncClient) -> None:
    response = await client.get(
        "/api/v1/locations", headers={"Authorization": "Basic dXNlcjpwYXNz"}
    )
    assert response.status_code == 401
    assert response.json()["error_code"] == "auth.credentials_required"


async def test_revoked_token_rejected(app: FastAPI, client: AsyncClient) -> None:
    _install_token(app, "test-engineer", "revoked-secret", revoked=True)
    response = await client.get(
        "/api/v1/locations", headers=auth_header_for_secret("revoked-secret")
    )
    assert response.status_code == 401
    assert response.json()["error_code"] == "auth.invalid_token"


async def test_expired_token_rejected(app: FastAPI, client: AsyncClient) -> None:
    _install_token(
        app,
        "test-engineer",
        "expired-secret",
        expires_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    response = await client.get(
        "/api/v1/locations", headers=auth_header_for_secret("expired-secret")
    )
    assert response.status_code == 401
    assert response.json()["error_code"] == "auth.invalid_token"


async def test_unexpired_token_accepted(app: FastAPI, client: AsyncClient) -> None:
    _install_token(
        app,
        "test-engineer",
        "fresh-secret",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    response = await client.get("/api/v1/locations", headers=auth_header_for_secret("fresh-secret"))
    assert response.status_code == 200


async def test_inactive_user_token_rejected(client: AsyncClient) -> None:
    users = await client.get("/api/v1/users", headers=ADMIN)
    viewer = next(item for item in users.json()["items"] if item["username"] == "test-viewer")
    deactivated = await client.post(f"/api/v1/users/{viewer['id']}/deactivate", headers=ADMIN)
    assert deactivated.status_code == 200

    response = await client.get("/api/v1/locations", headers=VIEWER)
    assert response.status_code == 401
    assert response.json()["error_code"] == "auth.invalid_token"


async def test_viewer_reads_but_cannot_write(client: AsyncClient) -> None:
    hierarchy = await create_hierarchy(client)

    listing = await client.get("/api/v1/locations", headers=VIEWER)
    assert listing.status_code == 200
    assert listing.json()["total"] == 4

    audit = await client.get("/api/v1/audit-entries", headers=VIEWER)
    assert audit.status_code == 200

    created = await client.post(
        "/api/v1/locations",
        json={"kind": "site", "code": "CAMP-V", "name": "Campus V"},
        headers=VIEWER,
    )
    assert created.status_code == 403
    assert created.json()["error_code"] == "auth.forbidden"

    patched = await client.patch(
        f"/api/v1/locations/{hierarchy['site']['id']}",
        json={"name": "Renamed"},
        headers=VIEWER,
    )
    assert patched.status_code == 403

    deleted = await client.delete(f"/api/v1/locations/{hierarchy['room']['id']}", headers=VIEWER)
    assert deleted.status_code == 403

    unchanged = await client.get("/api/v1/locations")
    assert unchanged.json()["total"] == 4


@pytest.mark.parametrize("headers_name", ["engineer", "viewer"])
async def test_user_management_requires_admin(client: AsyncClient, headers_name: str) -> None:
    headers = ACTOR if headers_name == "engineer" else VIEWER

    listing = await client.get("/api/v1/users", headers=headers)
    assert listing.status_code == 403
    assert listing.json()["error_code"] == "auth.forbidden"

    created = await client.post(
        "/api/v1/users",
        json={"username": "new-user", "display_name": "New User", "role": "viewer"},
        headers=headers,
    )
    assert created.status_code == 403

    tokens = await client.post(
        "/api/v1/users/any-id/tokens",
        json={"label": "nope"},
        headers=headers,
    )
    assert tokens.status_code == 403


async def test_admin_writes_domain_resources(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/locations",
        json={"kind": "site", "code": "CAMP-Z", "name": "Campus Z"},
        headers=ADMIN,
    )
    assert response.status_code == 201


# --- Throttling and lockout on failed token authentication -----------------

BAD = {"Authorization": "Bearer wrong-secret"}

DEFAULT_MAX_FAILURES = 10


class FakeClock:
    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def client_from(app: FastAPI, host: str) -> AsyncClient:
    """A client whose requests arrive from the given socket peer address."""
    transport = ASGITransport(app=app, client=(host, 40000))
    return AsyncClient(transport=transport, base_url="http://testserver")


def proxied_client(app: FastAPI, forwarded: str, *, peer: str = "127.0.0.1") -> AsyncClient:
    """A client sending ``X-Forwarded-For`` through uvicorn's own middleware.

    uvicorn installs :class:`ProxyHeadersMiddleware` unless it is started
    with ``--no-proxy-headers``, and trusts ``127.0.0.1`` unless
    ``--forwarded-allow-ips`` says otherwise. From a trusted peer it
    replaces the client address with an entry taken from the header before
    any application code runs. The real middleware is used here rather than
    an imitation of it, so these tests exercise what ``uvicorn
    cfm.main:app`` actually serves.
    """
    transport = ASGITransport(app=ProxyHeadersMiddleware(app), client=(peer, 40000))
    return AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"X-Forwarded-For": forwarded},
    )


def install_limiter(
    app: FastAPI,
    clock: FakeClock,
    *,
    max_failures: int = 3,
    window_seconds: float = 60,
    lockout_seconds: float = 300,
) -> None:
    app.state.auth_limiter = AuthRateLimiter(
        max_failures=max_failures,
        window_seconds=window_seconds,
        lockout_seconds=lockout_seconds,
        clock=clock,
    )


@pytest.fixture
async def proxy_app(database_url: str) -> AsyncIterator[FastAPI]:
    """An application that trusts one reverse proxy in front of it."""
    async with build_app(database_url, trusted_proxy=True) as application:
        yield application


async def test_failed_auths_lock_the_source(app: FastAPI) -> None:
    async with client_from(app, "192.0.2.1") as client:
        for _ in range(DEFAULT_MAX_FAILURES):
            response = await client.get("/api/v1/me", headers=BAD)
            assert response.status_code == 401
        locked = await client.get("/api/v1/me", headers=BAD)
        assert locked.status_code == 429
        assert locked.headers["content-type"] == "application/problem+json"
        assert locked.json()["error_code"] == "auth.rate_limited"
        assert 1 <= int(locked.headers["retry-after"]) <= 300


async def test_lockout_also_rejects_valid_tokens(app: FastAPI) -> None:
    async with client_from(app, "192.0.2.2") as client:
        for _ in range(DEFAULT_MAX_FAILURES):
            await client.get("/api/v1/me", headers=BAD)
        response = await client.get("/api/v1/me", headers=ACTOR)
        assert response.status_code == 429
        assert "retry-after" in response.headers


async def test_lockout_is_per_source(app: FastAPI) -> None:
    async with (
        client_from(app, "192.0.2.3") as abuser,
        client_from(app, "192.0.2.4") as bystander,
    ):
        for _ in range(DEFAULT_MAX_FAILURES):
            await abuser.get("/api/v1/me", headers=BAD)
        assert (await abuser.get("/api/v1/me", headers=ACTOR)).status_code == 429
        assert (await bystander.get("/api/v1/me", headers=ACTOR)).status_code == 200
        assert (await bystander.get("/api/v1/me", headers=BAD)).status_code == 401


async def test_successful_auths_are_never_counted(app: FastAPI) -> None:
    async with client_from(app, "192.0.2.5") as client:
        for _ in range(2 * DEFAULT_MAX_FAILURES + 5):
            response = await client.get("/api/v1/me", headers=ACTOR)
            assert response.status_code == 200
        # If successes counted, the source would already be locked.
        assert (await client.get("/api/v1/me", headers=BAD)).status_code == 401
        assert (await client.get("/api/v1/me", headers=ACTOR)).status_code == 200


async def test_a_success_does_not_reset_the_failure_count(app: FastAPI) -> None:
    async with client_from(app, "192.0.2.6") as client:
        for _ in range(DEFAULT_MAX_FAILURES - 1):
            assert (await client.get("/api/v1/me", headers=BAD)).status_code == 401
        assert (await client.get("/api/v1/me", headers=ACTOR)).status_code == 200
        assert (await client.get("/api/v1/me", headers=BAD)).status_code == 401
        assert (await client.get("/api/v1/me", headers=ACTOR)).status_code == 429


async def test_lockout_expires_after_the_configured_time(app: FastAPI) -> None:
    clock = FakeClock()
    install_limiter(app, clock, max_failures=3, lockout_seconds=300)
    async with client_from(app, "192.0.2.7") as client:
        for _ in range(3):
            assert (await client.get("/api/v1/me", headers=BAD)).status_code == 401
        locked = await client.get("/api/v1/me", headers=ACTOR)
        assert locked.status_code == 429
        assert locked.headers["retry-after"] == "300"
        clock.advance(299)
        nearly = await client.get("/api/v1/me", headers=ACTOR)
        assert nearly.status_code == 429
        assert nearly.headers["retry-after"] == "1"
        clock.advance(2)
        assert (await client.get("/api/v1/me", headers=ACTOR)).status_code == 200
        # The failure budget restarted: one more failure is a 401, not a 429.
        assert (await client.get("/api/v1/me", headers=BAD)).status_code == 401


async def test_locked_responses_do_not_reveal_whether_a_token_exists(app: FastAPI) -> None:
    clock = FakeClock()
    install_limiter(app, clock, max_failures=2, lockout_seconds=300)
    async with client_from(app, "192.0.2.8") as client:
        for _ in range(2):
            assert (await client.get("/api/v1/me", headers=BAD)).status_code == 401
        valid = await client.get("/api/v1/me", headers=ACTOR)
        invalid = await client.get("/api/v1/me", headers=BAD)
        missing = await client.get("/api/v1/me")
        assert valid.status_code == invalid.status_code == missing.status_code == 429
        assert valid.json() == invalid.json() == missing.json()
        assert (
            valid.headers["retry-after"]
            == invalid.headers["retry-after"]
            == missing.headers["retry-after"]
            == "300"
        )


async def test_429_uses_the_problem_details_shape(app: FastAPI) -> None:
    clock = FakeClock()
    install_limiter(app, clock, max_failures=1)
    async with client_from(app, "192.0.2.9") as client:
        await client.get("/api/v1/me", headers=BAD)
        response = await client.get("/api/v1/me", headers=BAD)
        assert response.status_code == 429
        body = response.json()
        assert body["status"] == 429
        assert body["title"]
        assert body["detail"]
        assert body["instance"] == "/api/v1/me"
        assert body["error_code"] == "auth.rate_limited"


async def test_forwarded_headers_share_one_bucket_without_trusted_proxy(app: FastAPI) -> None:
    async with client_from(app, "192.0.2.44") as client:
        for index in range(DEFAULT_MAX_FAILURES):
            headers = {**BAD, "X-Forwarded-For": f"10.0.0.{index}"}
            assert (await client.get("/api/v1/me", headers=headers)).status_code == 401
        # Varying the spoofable header did not spread the failures across
        # sources: every attempt carrying an untrusted header counted into
        # one bucket, and that bucket is locked.
        response = await client.get("/api/v1/me", headers={**ACTOR, "X-Forwarded-For": "10.0.0.99"})
        assert response.status_code == 429
        # The failures counted against the shared bucket, not against the
        # peer: traffic from the same peer without a header keeps its own
        # budget.
        assert (await client.get("/api/v1/me", headers=ACTOR)).status_code == 200


async def test_rotating_the_forwarded_header_does_not_evade_the_lockout(app: FastAPI) -> None:
    """The reported bypass, end to end through uvicorn's proxy handling.

    Ten failures arrive claiming one address and lock it; the eleventh
    claims another address. Attribution that follows a client-writable
    header would let it through, which is the whole attack.
    """
    async with proxied_client(app, "203.0.113.7") as attacker:
        for _ in range(DEFAULT_MAX_FAILURES):
            assert (await attacker.get("/api/v1/me", headers=BAD)).status_code == 401
        assert (await attacker.get("/api/v1/me", headers=BAD)).status_code == 429
    async with proxied_client(app, "198.51.100.9") as rotated:
        assert (await rotated.get("/api/v1/me", headers=BAD)).status_code == 429
        assert (await rotated.get("/api/v1/me", headers=ACTOR)).status_code == 429


async def test_a_fresh_forwarded_address_per_attempt_still_locks(app: FastAPI) -> None:
    for index in range(DEFAULT_MAX_FAILURES):
        async with proxied_client(app, f"203.0.113.{index}") as attacker:
            assert (await attacker.get("/api/v1/me", headers=BAD)).status_code == 401
    async with proxied_client(app, "198.51.100.9") as attacker:
        assert (await attacker.get("/api/v1/me", headers=ACTOR)).status_code == 429


async def test_the_shared_bucket_spares_traffic_without_a_forwarded_header(app: FastAPI) -> None:
    async with (
        proxied_client(app, "203.0.113.7") as spoofer,
        client_from(app, "192.0.2.30") as direct,
    ):
        for _ in range(DEFAULT_MAX_FAILURES):
            assert (await spoofer.get("/api/v1/me", headers=BAD)).status_code == 401
        assert (await spoofer.get("/api/v1/me", headers=ACTOR)).status_code == 429
        assert (await direct.get("/api/v1/me", headers=ACTOR)).status_code == 200
        assert (await direct.get("/api/v1/me", headers=BAD)).status_code == 401


async def test_the_shared_bucket_stays_out_of_the_trusted_proxy_path(proxy_app: FastAPI) -> None:
    async with proxied_client(proxy_app, "203.0.113.7") as first:
        for _ in range(DEFAULT_MAX_FAILURES):
            assert (await first.get("/api/v1/me", headers=BAD)).status_code == 401
        assert (await first.get("/api/v1/me", headers=ACTOR)).status_code == 429
    async with proxied_client(proxy_app, "198.51.100.9") as second:
        # With a proxy declared, attribution is per forwarded client again:
        # one client's lockout leaves the others alone.
        assert (await second.get("/api/v1/me", headers=ACTOR)).status_code == 200


async def test_trusted_proxy_attributes_failures_to_the_forwarded_client(
    proxy_app: FastAPI,
) -> None:
    async with client_from(proxy_app, "127.0.0.1") as client:
        for _ in range(DEFAULT_MAX_FAILURES):
            headers = {**BAD, "X-Forwarded-For": "203.0.113.7"}
            assert (await client.get("/api/v1/me", headers=headers)).status_code == 401
        locked = await client.get("/api/v1/me", headers={**ACTOR, "X-Forwarded-For": "203.0.113.7"})
        assert locked.status_code == 429
        # A left-hand entry the attacker prepends does not change the source:
        # only the rightmost entry, appended by the trusted proxy, is used.
        spoofed = await client.get(
            "/api/v1/me", headers={**ACTOR, "X-Forwarded-For": "198.51.100.1, 203.0.113.7"}
        )
        assert spoofed.status_code == 429
        other = await client.get("/api/v1/me", headers={**ACTOR, "X-Forwarded-For": "203.0.113.8"})
        assert other.status_code == 200
        direct = await client.get("/api/v1/me", headers=ACTOR)
        assert direct.status_code == 200
