from fastapi.testclient import TestClient

def test_cache_control_headers_present(client: TestClient):
    """
    Test that the Cache-Control header is present and has the correct value
    on all responses (verified via the /health endpoint).
    """
    response = client.get("/health")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store, no-cache, must-revalidate, private"

def test_cache_control_headers_on_api_endpoint(client: TestClient, test_token):
    """
    Test that the Cache-Control header is present on a protected API endpoint.
    """
    response = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {test_token}"}
    )
    # Even if auth fails or succeeds, the header should be there because it's a global middleware
    # But ideally we want a 200 to be sure we are checking a valid response
    if response.status_code == 200:
        assert response.headers["Cache-Control"] == "no-store, no-cache, must-revalidate, private"
    else:
        # If for some reason auth fails in this test setup,
        # we still expect the header.
        assert "Cache-Control" in response.headers
        assert response.headers["Cache-Control"] == "no-store, no-cache, must-revalidate, private"

def test_security_headers_present(client: TestClient):
    """
    Test that security headers are present on all responses.
    """
    response = client.get("/health")
    assert response.status_code == 200
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert response.headers["Permissions-Policy"] == "geolocation=(), microphone=(), camera=()"

def test_security_headers_on_api_endpoint(client: TestClient, test_token):
    """
    Test that security headers are present on protected API endpoints.
    """
    response = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {test_token}"}
    )
    assert "X-Frame-Options" in response.headers
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "X-Content-Type-Options" in response.headers
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "Referrer-Policy" in response.headers
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
