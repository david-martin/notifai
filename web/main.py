import logging
import os
import secrets
import time

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from web.limiter import limiter
from web.logging_config import configure_logging, reset_req_id, set_req_id
from web.routers import auth, billing, queries

configure_logging()

logger = logging.getLogger(__name__)

app = FastAPI(title="notifai")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

if os.environ.get("METRICS_ENABLED", "false").lower() == "true":
    from prometheus_fastapi_instrumentator import Instrumentator
    from prometheus_client import REGISTRY
    from web.metrics import NotifaiDBCollector

    # Prometheus HTTP instrumentation — adds /metrics endpoint and middleware.
    # Uses FastAPI route templates as handler labels (/queries/{query_id}/run)
    # to prevent cardinality explosion.
    Instrumentator().instrument(app).expose(app, include_in_schema=False)

    # DB-backed collector: surfaces runner health by querying NotificationLog/Query
    # at each 60s scrape. No Pushgateway needed.
    REGISTRY.register(NotifaiDBCollector())


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'"
    )
    return response


@app.middleware("http")
async def request_logging(request: Request, call_next):
    req_id = secrets.token_hex(4)
    token = set_req_id(req_id)
    t0 = time.monotonic()
    logger.info("request_start method=%s path=%s", request.method, request.url.path)
    try:
        response = await call_next(request)
        ms = int((time.monotonic() - t0) * 1000)
        logger.info("request_done status=%d duration_ms=%d", response.status_code, ms)
        return response
    except Exception:
        ms = int((time.monotonic() - t0) * 1000)
        logger.error("request_error duration_ms=%d", ms, exc_info=True)
        raise
    finally:
        reset_req_id(token)


app.include_router(auth.router)
app.include_router(queries.router)
app.include_router(billing.router)


@app.get("/health")
def health():
    return {"status": "ok"}


app.mount("/", StaticFiles(directory="web/static", html=True), name="static")
