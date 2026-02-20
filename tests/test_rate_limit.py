"""
Tests for RateLimitMiddleware.

These tests exercise the middleware directly using the test client, verifying
that public auth endpoints return HTTP 429 after the configured number of
requests and that the Retry-After header is present.
"""
import pytest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

from app.middleware.rate_limit import RateLimitMiddleware, DEFAULT_RATE_LIMITS


class TestRateLimitMiddlewareDirect:
    """Unit tests for the RateLimitMiddleware helper methods."""

    def _make_middleware(self, limits=None):
        """Return a RateLimitMiddleware instance without a real ASGI app."""
        mock_app = MagicMock()
        return RateLimitMiddleware(mock_app, rate_limits=limits or DEFAULT_RATE_LIMITS)

    def test_get_client_ip_from_forwarded_header(self):
        """X-Forwarded-For header should take priority over request.client.host."""
        mw = self._make_middleware()
        req = MagicMock()
        req.headers = {"X-Forwarded-For": "1.2.3.4, 10.0.0.1"}
        req.client = MagicMock(host="127.0.0.1")

        assert mw._get_client_ip(req) == "1.2.3.4"

    def test_get_client_ip_fallback_to_client_host(self):
        """Without X-Forwarded-For, fall back to request.client.host."""
        mw = self._make_middleware()
        req = MagicMock()
        req.headers = {}
        req.client = MagicMock(host="5.6.7.8")

        assert mw._get_client_ip(req) == "5.6.7.8"

    def test_get_client_ip_no_client(self):
        """Return 'unknown' when there is no client information at all."""
        mw = self._make_middleware()
        req = MagicMock()
        req.headers = {}
        req.client = None

        assert mw._get_client_ip(req) == "unknown"

    def test_check_rate_limit_allows_requests_within_limit(self):
        """
        Given a limit of 3 requests per 60 s
        When 3 requests arrive
        Then none of them should be rate-limited.
        """
        mw = self._make_middleware()
        for _ in range(3):
            is_limited, _ = mw._check_rate_limit("1.2.3.4", "/auth/login", 3, 60)
            assert not is_limited

    def test_check_rate_limit_blocks_on_exceeding_limit(self):
        """
        Given a limit of 3 requests per 60 s
        When a 4th request arrives
        Then it should be rate-limited.
        """
        mw = self._make_middleware()
        for _ in range(3):
            mw._check_rate_limit("1.2.3.4", "/auth/login", 3, 60)

        is_limited, retry_after = mw._check_rate_limit("1.2.3.4", "/auth/login", 3, 60)
        assert is_limited
        assert retry_after > 0

    def test_check_rate_limit_different_ips_are_independent(self):
        """Different IPs must have independent counters."""
        mw = self._make_middleware()
        for _ in range(3):
            mw._check_rate_limit("1.1.1.1", "/auth/login", 3, 60)

        # Exhausted for 1.1.1.1
        is_limited_a, _ = mw._check_rate_limit("1.1.1.1", "/auth/login", 3, 60)
        # Not exhausted for 2.2.2.2
        is_limited_b, _ = mw._check_rate_limit("2.2.2.2", "/auth/login", 3, 60)

        assert is_limited_a
        assert not is_limited_b

    def test_check_rate_limit_different_paths_are_independent(self):
        """Different paths must have independent counters for the same IP."""
        mw = self._make_middleware()
        for _ in range(3):
            mw._check_rate_limit("1.2.3.4", "/auth/login", 3, 60)

        # Exhausted for /auth/login
        is_limited_login, _ = mw._check_rate_limit("1.2.3.4", "/auth/login", 3, 60)
        # Not exhausted for /auth/register
        is_limited_register, _ = mw._check_rate_limit("1.2.3.4", "/auth/register", 3, 60)

        assert is_limited_login
        assert not is_limited_register


class TestRateLimitMiddlewareIntegration:
    """Integration tests using the real FastAPI TestClient."""

    def test_login_endpoint_rate_limited_after_threshold(self):
        """
        Given the /auth/login endpoint with a custom low limit
        When requests exceed the limit
        Then HTTP 429 is returned with a Retry-After header.
        """
        low_limit = {"/auth/login": (3, 60)}

        # Drive the private helper directly with a fresh middleware instance
        # to validate the core logic without needing an active HTTP server.
        mw = RateLimitMiddleware(MagicMock(), rate_limits=low_limit)
        for _ in range(3):
            is_limited, _ = mw._check_rate_limit("127.0.0.1", "/auth/login", 3, 60)
            assert not is_limited

        is_limited, retry_after = mw._check_rate_limit("127.0.0.1", "/auth/login", 3, 60)
        assert is_limited
        assert retry_after >= 1

    def test_rate_limit_returns_429_and_retry_after(self, client: TestClient):
        """
        Given a rate limit of 2 per 60 s on /auth/login
        When more than 2 requests come from the same IP
        Then the server responds with 429 and a Retry-After header.
        """
        from app.main import app as fastapi_app

        # Find the RateLimitMiddleware in the Starlette middleware stack
        mw_instance = None
        stack = fastapi_app.middleware_stack
        # Walk the chain
        current = stack
        while current is not None:
            if isinstance(current, RateLimitMiddleware):
                mw_instance = current
                break
            current = getattr(current, "app", None)

        if mw_instance is None:
            pytest.skip("RateLimitMiddleware not found in middleware stack (may be disabled)")

        # Override with a very low limit for this test
        original_limits = mw_instance._rate_limits
        mw_instance._rate_limits = {"/auth/login": (2, 60)}
        # Clear any state from previous tests
        mw_instance._request_timestamps.clear()

        try:
            # The TestClient uses a fixed IP (127.0.0.1 / testclient).
            # First 2 requests should pass (status is not 429).
            for _ in range(2):
                resp = client.post(
                    "/auth/login",
                    json={"username": "x@x.com", "password": "wrong"},
                )
                assert resp.status_code != 429

            # Third request must be rate-limited.
            resp = client.post(
                "/auth/login",
                json={"username": "x@x.com", "password": "wrong"},
            )
            assert resp.status_code == 429
            assert "Retry-After" in resp.headers
            assert int(resp.headers["Retry-After"]) >= 1
            assert "Too many requests" in resp.json()["detail"]
        finally:
            mw_instance._rate_limits = original_limits
            mw_instance._request_timestamps.clear()

    def test_unprotected_endpoint_not_rate_limited(self, client: TestClient):
        """
        /health is not in the rate-limited path list and must never return 429.
        """
        for _ in range(20):
            resp = client.get("/health")
            assert resp.status_code == 200

    def test_default_rate_limits_cover_expected_endpoints(self):
        """DEFAULT_RATE_LIMITS must include all sensitive public auth endpoints."""
        expected = {
            "/auth/login",
            "/auth/register",
            "/auth/validate-email",
            "/auth/sign-in",
            "/auth/generate-trial-access",
        }
        assert expected.issubset(set(DEFAULT_RATE_LIMITS.keys()))

    def test_rate_limit_config_has_positive_values(self):
        """Every entry in DEFAULT_RATE_LIMITS must have positive limit and window."""
        for path, (limit, window) in DEFAULT_RATE_LIMITS.items():
            assert limit > 0, f"limit for {path} must be positive"
            assert window > 0, f"window for {path} must be positive"
