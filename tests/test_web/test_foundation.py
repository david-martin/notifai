from sqlalchemy import inspect


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_csp_script_src_has_no_unsafe_inline(client):
    response = client.get("/health")
    csp = response.headers.get("Content-Security-Policy", "")
    script_src = next((d for d in csp.split(";") if "script-src" in d), "")
    assert "'unsafe-inline'" not in script_src, \
        f"script-src must not contain 'unsafe-inline', got: {script_src}"


def test_security_headers_present(client):
    response = client.get("/health")
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "DENY"
    assert "Strict-Transport-Security" in response.headers
    assert "Permissions-Policy" in response.headers




def test_all_tables_created(db_engine):
    inspector = inspect(db_engine)
    tables = set(inspector.get_table_names())
    assert "users" in tables
    assert "magic_links" in tables
    assert "sessions" in tables
    assert "queries" in tables
    assert "notification_log" in tables


def test_users_table_columns(db_engine):
    inspector = inspect(db_engine)
    columns = {c["name"] for c in inspector.get_columns("users")}
    assert {"id", "email", "notify_email", "tier", "query_credits", "created_at", "low_balance_notified"}.issubset(columns)
    # Dead columns dropped in migration e1f2a3b4c5d6
    assert "creation_attempts_this_month" not in columns
    assert "creation_attempts_reset_at" not in columns


def test_queries_table_columns(db_engine):
    inspector = inspect(db_engine)
    columns = {c["name"] for c in inspector.get_columns("queries")}
    assert {"id", "user_id", "query_text",
            "active", "created_at"}.issubset(columns)
    assert "notify_on_no" not in columns


def test_user_has_low_balance_notified_column(db):
    from web.models import User
    user = User(email="t@example.com", query_credits=5, tier="free")
    db.add(user)
    db.commit()
    db.refresh(user)
    assert user.low_balance_notified is False
