from app.middleware.prometheus import auth_requests_total


def _counter(status: str) -> float:
    return auth_requests_total.labels(
        endpoint="/auth/login", status=status
    )._value.get()


def test_auth_metric_counts_failed_login(client, test_user):
    before_failure = _counter("failure")
    before_success = _counter("success")

    response = client.post(
        "/auth/login", data={"username": test_user.email, "password": "wrongpassword"}
    )
    assert response.status_code == 401

    assert _counter("failure") == before_failure + 1
    assert _counter("success") == before_success


def test_auth_metric_counts_successful_login(client, test_user):
    before_failure = _counter("failure")
    before_success = _counter("success")

    response = client.post(
        "/auth/login", data={"username": test_user.email, "password": "testpassword"}
    )
    assert response.status_code == 200

    assert _counter("success") == before_success + 1
    assert _counter("failure") == before_failure
