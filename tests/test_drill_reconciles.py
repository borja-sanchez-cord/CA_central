"""
Drill-through reconciliation — the click-peek card must count EXACTLY what the
clicked chart mark counted, for every mapping, forever.

Why this exists (PM, 2026-07-22): the chart numbers are computed by SQL
(rep_scorecard / rep_meeting_breakdown), while the drill card re-counts with
its own filter built from the measure->filter maps in dashboard/queries.py.
Those are TWO copies of each definition. The dashboard makes a drift visible
(the card prints its own total next to the mark), but this test makes it
BLOCKING: change a definition on one side without the other and the suite
goes red before anything deploys.

Needs a database (SUPABASE_DB_URL or DASHBOARD_DB_URL, .env or CI secret) —
read-only queries against the same approved surfaces the dashboard reads.
Skips cleanly when no URL is around.
"""
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "dashboard"))
import queries  # pure SQL strings + the drill maps; no streamlit needed


def _db_url():
    # mirror db.py's lightweight .env loading so local runs Just Work
    path = os.path.join(_HERE, "..", ".env")
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k, v.strip())
    return os.environ.get("DASHBOARD_DB_URL") or os.environ.get("SUPABASE_DB_URL")


DB_URL = _db_url()
pytestmark = pytest.mark.skipif(
    not DB_URL, reason="no database URL (SUPABASE_DB_URL / DASHBOARD_DB_URL)")


@pytest.fixture(scope="module")
def cur():
    psycopg2 = pytest.importorskip("psycopg2")
    conn = psycopg2.connect(DB_URL, connect_timeout=20)
    yield conn.cursor()
    conn.rollback()
    conn.close()


@pytest.fixture(scope="module")
def windows(cur):
    """The same windows the pages offer: last-7-days, all-time (both relative
    to the data's own edge, so the test is deterministic on any day)."""
    cur.execute("select min(activity_date), max(activity_date) from activity_flat where counts")
    first, last = cur.fetchone()
    import datetime as dt
    return [(max(last - dt.timedelta(days=6), first), last), (first, last)]


def _drill_total(cur, start, end, rep, channels, outcome="all"):
    cur.execute(queries.DRILL_ROWS,
                (start, end, rep, rep, channels, "(all)", "(all)",
                 outcome, outcome, outcome, outcome))
    rows = cur.fetchall()
    return rows[0][-1] if rows else 0     # `total` window column; 0 rows = 0


def _bucket_total(cur, start, end, rep, bucket):
    cur.execute(queries.DRILL_MEETING_ROWS, (start, end, rep, rep, bucket))
    rows = cur.fetchall()
    return rows[0][-1] if rows else 0


def _scorecard(cur, start, end):
    """Per-CA scorecard rows for ACTIVE CAs — the numbers the charts draw."""
    cur.execute("""
        select s.* from rep_scorecard(%s, %s) s
        join dim_ca c on c.ca_id = s.ca_id
        where c.is_active""", (start, end))
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


# ---------------------------------------------------------------- the audits
def test_channel_measures_reconcile_team_and_per_ca(cur, windows):
    """Every DRILL_CHANNELS mapping == the scorecard column it stands for,
    team-wide AND for every single CA, in both windows."""
    ALIAS = {"calls": "dials"}          # Per-CA account chart uses 'calls'
    for start, end in windows:
        sc = _scorecard(cur, start, end)
        for measure, channels in queries.DRILL_CHANNELS.items():
            col = ALIAS.get(measure, measure)
            want_team = sum(r[col] for r in sc)
            got_team = _drill_total(cur, start, end, "(all)", channels)
            assert got_team == want_team, (
                "TEAM %s %s..%s: card %d != chart %d" %
                (measure, start, end, got_team, want_team))
            for r in sc:
                got = _drill_total(cur, start, end, r["ca_name"], channels)
                assert got == r[col], (
                    "%s %s %s..%s: card %d != chart %d" %
                    (r["ca_name"], measure, start, end, got, r[col]))


def test_meeting_outcomes_reconcile(cur, windows):
    """The meetings-outcome bars (held/canceled/scheduled/unknown)."""
    for start, end in windows:
        sc = _scorecard(cur, start, end)
        for status, param in queries.OUTCOME_PARAM.items():
            col = "meetings_" + status
            want_team = sum(r[col] for r in sc)
            got_team = _drill_total(cur, start, end, "(all)", ["meeting"], param)
            assert got_team == want_team, (
                "TEAM meetings %s %s..%s: card %d != chart %d" %
                (status, start, end, got_team, want_team))
            for r in sc:
                got = _drill_total(cur, start, end, r["ca_name"], ["meeting"], param)
                assert got == r[col], (
                    "%s meetings %s: card %d != chart %d" %
                    (r["ca_name"], status, got, r[col]))


def test_meeting_buckets_reconcile(cur, windows):
    """The 60-day new-stakeholder / follow-up / no-account bars."""
    for start, end in windows:
        cur.execute("""
            select b.* from rep_meeting_breakdown(%s, %s) b
            join dim_ca c on c.ca_id = b.ca_id
            where c.is_active""", (start, end))
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        for bucket in ("new_stakeholder", "follow_up", "no_account"):
            col = "meetings_" + bucket
            want_team = sum(r[col] for r in rows)
            got_team = _bucket_total(cur, start, end, "(all)", bucket)
            assert got_team == want_team, (
                "TEAM bucket %s %s..%s: card %d != chart %d" %
                (bucket, start, end, got_team, want_team))
            for r in rows:
                got = _bucket_total(cur, start, end, r["ca_name"], bucket)
                assert got == r[col], (
                    "%s bucket %s: card %d != chart %d" %
                    (r["ca_name"], bucket, got, r[col]))


def test_channel_vocabulary_is_complete(cur):
    """ALL_CHANNELS must cover every channel that can carry a COUNTED row —
    a channel added to the model but not the map would silently vanish from
    the 'Activities' drill (the totals test above would also catch it, but
    this names the culprit). Never-counted channels (the call_task/email_task
    to-do shadows, excluded by rule) are out of scope: the drill peeks at
    counted rows only."""
    cur.execute("select distinct channel from activity_flat where counts")
    live = {r[0] for r in cur.fetchall()}
    assert live <= set(queries.ALL_CHANNELS), (
        "counted channels missing from queries.ALL_CHANNELS: %s"
        % sorted(live - set(queries.ALL_CHANNELS)))
