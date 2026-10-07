import pytest
from fastapi.testclient import TestClient

from app.core.cors_config import DEFAULT_CORS_ORIGINS, get_cors_origins


def test_default_cors_origins_include_localhost_aliases():
    origins = get_cors_origins(None)

    assert origins == list(DEFAULT_CORS_ORIGINS)
    assert "http://localhost:3001" in origins
    assert "http://127.0.0.1:3001" in origins


def test_api_preflight_allows_local_development_frontend():
    from app.main import app

    response = TestClient(app).options(
        "/api/v1/chat/clarify",
        headers={
            "Origin": "http://127.0.0.1:3001",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:3001"


def test_configured_origins_are_trimmed_deduplicated_and_exact():
    assert get_cors_origins(" https://app.example.com/,https://app.example.com ") == [
        "https://app.example.com"
    ]


@pytest.mark.parametrize("value", ["*", "https://example.com/path", "ftp://example.com"])
def test_invalid_cors_origins_are_rejected(value):
    with pytest.raises(ValueError, match="Invalid CORS origin"):
        get_cors_origins(value)
