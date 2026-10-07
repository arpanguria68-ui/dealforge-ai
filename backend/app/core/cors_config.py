from urllib.parse import urlsplit


DEFAULT_CORS_ORIGINS = (
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3001",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5174",
    "http://localhost:5175",
    "http://127.0.0.1:5175",
)


def get_cors_origins(configured_origins: str | None) -> list[str]:
    """Return explicit CORS origins, rejecting wildcard and malformed entries."""
    if configured_origins is None or not configured_origins.strip():
        return list(DEFAULT_CORS_ORIGINS)

    origins = list(dict.fromkeys(
        origin.strip().rstrip("/")
        for origin in configured_origins.split(",")
        if origin.strip()
    ))
    for origin in origins:
        parsed = urlsplit(origin)
        if origin == "*" or parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path:
            raise ValueError(f"Invalid CORS origin: {origin!r}")
    return origins
