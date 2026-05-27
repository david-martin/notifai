"""Tests for web/metrics.py — metric objects and DB collector."""
import pytest
from prometheus_client import CollectorRegistry
from sqlalchemy.orm import sessionmaker

from web.metrics import (
    NotifaiDBCollector,
    claude_api_calls_total,
    email_sends_total,
    query_execution_duration_seconds,
    query_executions_total,
)


@pytest.fixture
def metrics_db(db_engine):
    """Raw SQLAlchemy session for collector tests."""
    Session = sessionmaker(bind=db_engine)
    db = Session()
    yield db
    db.close()


class TestMetricObjects:
    def test_query_executions_total_label_names(self):
        assert query_executions_total._labelnames == ("trigger", "answer")

    def test_query_execution_duration_seconds_label_names(self):
        assert query_execution_duration_seconds._labelnames == ("trigger",)

    def test_claude_api_calls_total_label_names(self):
        assert claude_api_calls_total._labelnames == ("trigger", "outcome")

    def test_email_sends_total_label_names(self):
        assert email_sends_total._labelnames == ("trigger", "outcome")

    def test_query_executions_total_name(self):
        # Counter stores original name (with _total) in _original_name; _name strips it
        assert query_executions_total._original_name == "notifai_query_executions_total"

    def test_claude_api_calls_total_name(self):
        assert claude_api_calls_total._original_name == "notifai_claude_api_calls_total"

    def test_email_sends_total_name(self):
        assert email_sends_total._original_name == "notifai_email_sends_total"

    def test_query_execution_duration_seconds_name(self):
        assert query_execution_duration_seconds._name == "notifai_query_execution_duration_seconds"


class TestNotifaiDBCollector:
    def test_collect_yields_expected_metric_names(self, metrics_db):
        registry = CollectorRegistry()
        collector = NotifaiDBCollector(session_factory=lambda: metrics_db)
        registry.register(collector)
        names = {m.name for m in collector.collect()}
        assert "notifai_runner_last_run_timestamp_seconds" in names
        assert "notifai_active_queries" in names
        assert "notifai_users_with_credits" in names
        # CounterMetricFamily.name strips _total suffix; samples retain it
        assert "notifai_notifications_sent" in names

    def test_runner_timestamp_is_zero_with_no_logs(self, metrics_db):
        registry = CollectorRegistry()
        collector = NotifaiDBCollector(session_factory=lambda: metrics_db)
        registry.register(collector)
        metrics = {m.name: m for m in collector.collect()}
        ts_samples = list(metrics["notifai_runner_last_run_timestamp_seconds"].samples)
        assert ts_samples[0].value == 0.0

    def test_active_queries_count_reflects_db(self, metrics_db):
        from web.models import Query, User
        user = User(email="t@t.com", query_credits=5)
        metrics_db.add(user)
        metrics_db.flush()
        metrics_db.add(Query(user_id=user.id, query_text="test query", active=True))
        metrics_db.commit()

        registry = CollectorRegistry()
        collector = NotifaiDBCollector(session_factory=lambda: metrics_db)
        registry.register(collector)
        metrics_map = {m.name: m for m in collector.collect()}
        samples = list(metrics_map["notifai_active_queries"].samples)
        assert samples[0].value == 1.0

    def test_users_with_credits_count(self, metrics_db):
        from web.models import User
        metrics_db.add(User(email="a@a.com", query_credits=5))
        metrics_db.add(User(email="b@b.com", query_credits=0))
        metrics_db.commit()

        registry = CollectorRegistry()
        collector = NotifaiDBCollector(session_factory=lambda: metrics_db)
        registry.register(collector)
        metrics_map = {m.name: m for m in collector.collect()}
        samples = list(metrics_map["notifai_users_with_credits"].samples)
        assert samples[0].value == 1.0

class TestMetricsEndpoint:
    def test_metrics_endpoint_returns_200(self, client):
        r = client.get("/metrics")
        assert r.status_code == 200

    def test_metrics_endpoint_contains_http_metric(self, client):
        client.get("/health")
        r = client.get("/metrics")
        assert "http_requests_total" in r.text

    def test_metrics_endpoint_contains_notifai_metric(self, client):
        r = client.get("/metrics")
        assert "notifai_" in r.text


    def test_notifications_sent_total_has_yes_and_no_labels(self, metrics_db):
        from web.models import NotificationLog, Query, User
        user = User(email="x@x.com", query_credits=5)
        metrics_db.add(user)
        metrics_db.flush()
        q = Query(user_id=user.id, query_text="test", active=True)
        metrics_db.add(q)
        metrics_db.flush()
        metrics_db.add(NotificationLog(query_id=q.id, user_id=user.id, answer="YES", email_sent=False))
        metrics_db.add(NotificationLog(query_id=q.id, user_id=user.id, answer="NO", email_sent=False))
        metrics_db.commit()

        registry = CollectorRegistry()
        collector = NotifaiDBCollector(session_factory=lambda: metrics_db)
        registry.register(collector)
        metrics_map = {m.name: m for m in collector.collect()}
        # CounterMetricFamily.name strips _total; samples still have _total in sample.name
        family = metrics_map["notifai_notifications_sent"]
        samples = {s.labels.get("answer"): s.value for s in family.samples
                   if s.name.endswith("_total")}
        assert samples.get("YES") == 1.0
        assert samples.get("NO") == 1.0
