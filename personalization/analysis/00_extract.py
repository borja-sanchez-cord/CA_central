"""Stage 0: pull the counted-outbound-email corpus once to local parquet.
SELECT-only. Stores raw body_html; no bodies are printed to stdout."""
import os, psycopg2, pandas as pd

for line in open(".env"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k, v.strip())

conn = psycopg2.connect(os.environ["SUPABASE_DB_URL"], connect_timeout=30)
q = """
select activity_id, ca_name, channel, subject_norm, contact_firstname,
       contact_lastname, account_name, contact_jobtitle, contact_email,
       occurred_at, body_html
from activity_flat
where counts and channel in ('auto_email','manual_email')
"""
df = pd.read_sql(q, conn)
conn.close()

out = "reports/personalization/corpus_raw.parquet"
df.to_parquet(out, index=False)
print("rows:", len(df))
print("cols:", list(df.columns))
print("cas:", df.ca_name.nunique(), "| accounts:", df.account_name.nunique())
print("date range:", df.occurred_at.min(), "->", df.occurred_at.max())
print("body_html null:", df.body_html.isnull().sum())
print("wrote", out)
