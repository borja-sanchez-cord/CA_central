#!/usr/bin/env python3
"""
Load Ray's "Global CA Performance Tracker — SAO Monthly Performance" CSV
export into the sao_monthly table (full refresh, single transaction).

    python3 sao/load_sao.py <path-to-csv>

The sheet is one wide tab: months as columns (newest left), six stacked
blocks per rep (SAOs Achieved / % Target Hit / SAOs Target / Pipeline
Created / Inbounds SAOs / Event SAOs). Two hard-won parsing facts:

  * Per-rep rows in EVERY block align to the sheet's GLOBAL month layout
    (the "SAOs Achieved" header row) — block-local headers can omit the
    newest months, so they are ignored.
  * The sheet is mirrored as-is, quirks included. The only interpretation
    is name normalization: roster reps are stored under dim_ca.name
    (see REP_ALIASES); former/inactive reps keep Ray's spelling.

After loading, rep-level sums per month are checksummed against the
sheet's own "Overall" row and any drift is printed — the sheet audits
itself. Ray's CSV is individual performance data: it must NEVER be
committed to git (same rule as reports/).
"""
import csv
import os
import sys
from datetime import datetime

import psycopg2

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ingestion"))
from ingest import load_env, require

# Ray's spelling -> dim_ca.name (only where they differ)
REP_ALIASES = {
    "Constantin Ertel": "Constantin Victor Beat Ertel",
}

# cell B value that starts a block -> sao_monthly column (None = skip block)
BLOCKS = {
    "SAOs Achieved": "saos",
    "% Target Hit": None,          # recomputed as saos/sao_target, never stored
    "SAOs Target": "sao_target",
    "Pipeline Created": "pipeline_usd",
    "Inbounds SAOs": "saos_inbound",
    "Event SAOs": "saos_event",
    "Overall CA Performance": None,
}

# aggregate / header-furniture rows that are never a rep
NON_REP_ROWS = {
    "", "Overall", "Overall %", "UK CA", "US CA", "Reps in Seat",
    "Ramping Reps", "SAOs per CA (effectiveness)", "SAO ACV Band",
    "Forecasted SAOs", "Nuances", "SAOs per rep in seat", "Inbounds SAOs",
}


def parse_month(cell):
    try:
        return datetime.strptime(cell.strip(), "%b %Y").date().replace(day=1)
    except ValueError:
        return None


def parse_date(cell):
    try:
        return datetime.strptime(cell.strip(), "%d %b %Y").date()
    except ValueError:
        return None


def parse_num(cell):
    """'3'->3, '$488K'->488000, '$4.1M'->4100000, ''/#VALUE!/#DIV/0! -> None."""
    s = cell.strip().replace(",", "")
    if not s or s.startswith("#"):
        return None
    mult = 1
    if s.startswith("$"):
        s = s[1:]
    if s.endswith("K"):
        s, mult = s[:-1], 1_000
    elif s.endswith("M"):
        s, mult = s[:-1], 1_000_000
    try:
        return float(s) * mult
    except ValueError:
        return None


def parse_sheet(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))

    # global month layout: the "SAOs Achieved" header row
    month_by_col = {}
    for r in rows:
        if len(r) > 1 and r[1].strip() == "SAOs Achieved":
            for i, cell in enumerate(r):
                m = parse_month(cell)
                if m:
                    month_by_col[i] = m
            break
    if len(month_by_col) < 12:
        sys.exit("could not find the global month header row — sheet layout changed?")

    data = {}      # (rep, month) -> {column: value}
    meta = {}      # rep -> dict of status/team/dates/ramping (from Achieved block)
    overall = {}   # month -> sheet's own Overall SAOs (checksum)
    block = None

    for r in rows:
        name = r[1].strip() if len(r) > 1 else ""
        if name in BLOCKS:
            block = BLOCKS[name]
            in_achieved = (name == "SAOs Achieved")
            continue
        if block is None:
            continue
        if name == "Overall" and in_achieved:
            for i, month in month_by_col.items():
                v = parse_num(r[i]) if i < len(r) else None
                if v is not None:
                    overall[month] = v
            continue
        if name in NON_REP_ROWS:
            continue

        rep = REP_ALIASES.get(name, name)
        if in_achieved:
            meta[rep] = {
                "is_ramping": r[2].strip() == "(Ramping)" if len(r) > 2 else False,
                "start_date": parse_date(r[4]) if len(r) > 4 else None,
                "end_date": parse_date(r[5]) if len(r) > 5 else None,
                "status": r[7].strip() or None if len(r) > 7 else None,
                "team": r[8].strip() or None if len(r) > 8 else None,
            }
        for i, month in month_by_col.items():
            v = parse_num(r[i]) if i < len(r) else None
            if v is not None:
                data.setdefault((rep, month), {})[block] = v

    return data, meta, overall


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: python3 sao/load_sao.py <path-to-csv>")
    data, meta, overall = parse_sheet(sys.argv[1])

    load_env()
    conn = psycopg2.connect(require("SUPABASE_DB_URL"), connect_timeout=20)
    with conn.cursor() as cur:
        cur.execute("select name from dim_ca")
        roster = {r[0] for r in cur.fetchall()}

        cur.execute("delete from sao_monthly")
        for (rep, month), vals in sorted(data.items()):
            m = meta.get(rep, {})
            cur.execute(
                """insert into sao_monthly (rep_name, month, saos, sao_target,
                       saos_inbound, saos_event, pipeline_usd, status,
                       is_ramping, team, start_date, end_date)
                   values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (rep, month, vals.get("saos"), vals.get("sao_target"),
                 vals.get("saos_inbound"), vals.get("saos_event"),
                 vals.get("pipeline_usd"), m.get("status"), m.get("is_ramping"),
                 m.get("team"), m.get("start_date"), m.get("end_date")))
    conn.commit()

    reps = {rep for rep, _ in data}
    print("loaded %d rep-month rows, %d reps, %s -> %s"
          % (len(data), len(reps),
             min(m for _, m in data), max(m for _, m in data)))

    # checksum: rep-level sums vs the sheet's own Overall row
    bad = 0
    for month, sheet_total in sorted(overall.items()):
        rep_sum = sum(v.get("saos", 0) for (rp, mo), v in data.items() if mo == month)
        if rep_sum != sheet_total:
            bad += 1
            print("  CHECKSUM DRIFT %s: sheet Overall=%g, sum of reps=%g"
                  % (month, sheet_total, rep_sum))
    print("checksum vs sheet's Overall row: %d/%d months match"
          % (len(overall) - bad, len(overall)))

    on_roster = reps & roster
    print("roster reps with SAO data: %d/%d (missing: %s)"
          % (len(on_roster), len(roster), sorted(roster - reps) or "none"))


if __name__ == "__main__":
    main()
