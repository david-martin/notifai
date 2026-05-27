"""Prometheus metrics for notifai.

All metric objects are defined here. Callsites import and observe — no metric
is defined at the callsite.

The NotifaiDBCollector queries the DB at scrape time to surface runner health
without requiring Pushgateway. Register it in main.py, not here, so this
module is importable in tests without side effects.
"""
import logging

from prometheus_client import Counter, Histogram
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily
from prometheus_client.registry import Collector
from sqlalchemy import func

from web.database import SessionLocal
from web.models import NotificationLog, Query, User

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-process metrics (observed at callsites in queries.py and runner.py)
# ---------------------------------------------------------------------------

query_executions_total = Counter(
    "notifai_query_executions_total",
    "Query executions by trigger and answer",
    ["trigger", "answer"],  # trigger: manual|scheduled; answer: YES|NO|error
)

query_execution_duration_seconds = Histogram(
    "notifai_query_execution_duration_seconds",
    "End-to-end query execution duration (API call + parse)",
    ["trigger"],
    buckets=[1, 2, 5, 10, 20, 30, 60, 120],
)

claude_api_calls_total = Counter(
    "notifai_claude_api_calls_total",
    "Claude API calls by trigger and outcome",
    ["trigger", "outcome"],  # outcome: success|search_failure|parse_error|api_error
)

email_sends_total = Counter(
    "notifai_email_sends_total",
    "Email send attempts by trigger and outcome",
    ["trigger", "outcome"],  # outcome: success|error
)


# ---------------------------------------------------------------------------
# DB-backed collector (surfaces runner health without Pushgateway)
# ---------------------------------------------------------------------------

class NotifaiDBCollector(Collector):
    """Queries the DB at scrape time to surface runner health and lifetime stats.

    The runner is a separate process (systemd oneshot) — it cannot write to
    the web process's in-memory registry. Instead, this collector reads
    NotificationLog and Query tables at each 60s scrape interval.
    Four cheap SELECT COUNT / SELECT MAX queries on a small SQLite DB.
    """

    def __init__(self, session_factory=None):
        self._session_factory = session_factory or SessionLocal

    def collect(self):
        db = self._session_factory()
        try:
            # Runner staleness — alert if time() - this > 90000s (25h)
            last_run = db.query(func.max(NotificationLog.checked_at)).scalar()
            g = GaugeMetricFamily(
                "notifai_runner_last_run_timestamp_seconds",
                "Unix timestamp of most recent runner check (0 if never run)",
            )
            g.add_metric([], last_run.timestamp() if last_run else 0.0)
            yield g

            # Lifetime notification counts by answer (counter semantics)
            c = CounterMetricFamily(
                "notifai_notifications_sent_total",
                "Lifetime notification log entries by answer",
                labels=["answer"],
            )
            for answer in ("YES", "NO"):
                count = (
                    db.query(NotificationLog)
                    .filter(NotificationLog.answer == answer)
                    .count()
                )
                c.add_metric([answer], float(count))
            yield c

            # Active queries currently monitored
            active = db.query(Query).filter(Query.active == True).count()  # noqa: E712
            g2 = GaugeMetricFamily(
                "notifai_active_queries",
                "Number of currently active queries",
            )
            g2.add_metric([], float(active))
            yield g2

            # Users who still have credits to run queries
            funded = db.query(User).filter(User.query_credits > 0).count()
            g3 = GaugeMetricFamily(
                "notifai_users_with_credits",
                "Number of users with query_credits > 0",
            )
            g3.add_metric([], float(funded))
            yield g3

        except Exception:
            logger.exception("db_collector_error")
        finally:
            db.close()
