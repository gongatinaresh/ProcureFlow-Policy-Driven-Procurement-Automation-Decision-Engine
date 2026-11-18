from fastapi.testclient import TestClient


def test_health_endpoint_checks_database(test_client: TestClient) -> None:
    response = test_client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "database": "ok",
        "service": "Cantill Procurement API",
    }
    assert response.headers["x-request-id"]


def test_unknown_route_uses_centralized_error_response(test_client: TestClient) -> None:
    response = test_client.get("/api/not-a-route")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "http_error"
    assert response.json()["error"]["request_id"] == response.headers["x-request-id"]


def test_frontend_origin_is_allowed_by_cors(test_client: TestClient) -> None:
    response = test_client.options(
        "/api/health",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
