from sqlalchemy import inspect


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


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
    assert {"id", "email", "notify_email", "tier", "creation_attempts_this_month",
            "creation_attempts_reset_at", "created_at"}.issubset(columns)


def test_queries_table_columns(db_engine):
    inspector = inspect(db_engine)
    columns = {c["name"] for c in inspector.get_columns("queries")}
    assert {"id", "user_id", "query_text",
            "active", "notify_on_no", "created_at"}.issubset(columns)
