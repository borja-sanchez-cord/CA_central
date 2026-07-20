"""
Dashboard database access — READ-ONLY by construction.

Connects as the dashboard_reader role (DASHBOARD_DB_URL), which can only
SELECT from the approved surfaces (see migrations/004 + docs/dashboard.md).
Even a bug in this app cannot write, delete or alter anything — Postgres
refuses, not us.
"""
import os
from decimal import Decimal

import pandas as pd
import psycopg2
import streamlit as st

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_env():
    path = os.path.join(_ROOT, ".env")
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k, v.strip())


def _url():
    _load_env()
    url = os.environ.get("DASHBOARD_DB_URL")
    if not url:
        try:
            url = st.secrets["DASHBOARD_DB_URL"]
        except Exception:
            url = None
    if not url:
        st.error("DASHBOARD_DB_URL is not configured (.env or Streamlit secrets).")
        st.stop()
    return url


@st.cache_resource
def _conn():
    return psycopg2.connect(_url(), connect_timeout=20)


@st.cache_data(ttl=300, show_spinner=False)
def q(sql, params=None):
    """Run a SELECT, return a DataFrame (Decimals -> floats for charting)."""
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
        conn.rollback()
    except psycopg2.OperationalError:
        _conn.clear()                      # stale pooler connection: retry once
        conn = _conn()
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
        conn.rollback()
    df = pd.DataFrame(rows, columns=cols)
    for c in df.columns:
        if df[c].map(lambda v: isinstance(v, Decimal)).any():
            df[c] = df[c].astype(float)
    return df
