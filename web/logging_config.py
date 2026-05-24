"""
Logging configuration for the notifai web app.

Sets up a plain-text formatter that injects a per-request trace ID
(req_id) into every log line.  The req_id is stored in a ContextVar and
set by the request-logging middleware in main.py.

Usage in any module:
    import logging
    logger = logging.getLogger(__name__)
    logger.info("event_name key1=%s key2=%d", val1, val2)

Every line produced by a web.* logger will look like:
    [INFO    ] queries      [a3f2b1c0] run_query_start query_id=fa097f74 user=me@example.com
"""
import contextvars
import logging

_req_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("req_id", default="-")


def get_req_id() -> str:
    """Return the current request's trace ID, or '-' when outside a request."""
    return _req_id_var.get()


def set_req_id(value: str) -> contextvars.Token:
    """Set the current request's trace ID.  Returns a token for reset_req_id()."""
    return _req_id_var.set(value)


def reset_req_id(token: contextvars.Token) -> None:
    """Restore the trace ID to its previous value (call in middleware finally block)."""
    _req_id_var.reset(token)


class _ReqIdFormatter(logging.Formatter):
    """Logging formatter that injects req_id from the current ContextVar."""

    def format(self, record: logging.LogRecord) -> str:
        record.req_id = _req_id_var.get()  # type: ignore[attr-defined]
        return super().format(record)


def configure_logging() -> None:
    """Configure app-wide logging.  Call once at startup (web/main.py).

    Attaches a StreamHandler to the 'web' logger namespace so all web.*
    loggers inherit the formatter.  propagate=False prevents double-logging
    if uvicorn or another framework also attaches a root handler.
    """
    web_log = logging.getLogger("web")
    if web_log.handlers:
        return  # already configured (e.g. called twice in tests)
    handler = logging.StreamHandler()
    handler.setFormatter(
        _ReqIdFormatter(fmt="[%(levelname)-8s] %(module)-12s [%(req_id)s] %(message)s")
    )
    web_log.setLevel(logging.INFO)
    web_log.addHandler(handler)
    web_log.propagate = False
