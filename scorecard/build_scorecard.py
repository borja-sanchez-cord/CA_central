#!/usr/bin/env python3
"""Phase 4 scorecard workbook — renders the rep_scorecard() Supabase function
into a presentable Excel for the PM / sales leaders.

READ-ONLY: this script only SELECTs from the Phase 4 views. The database
objects it reads are created by migrations/002_rep_scorecard.sql; the
definitions of every column are in docs/ontology.md.

Run:  python scorecard/build_scorecard.py
Output: reports/CA_rep_scorecard_<today>.xlsx  (reports/ is gitignored -
per-rep performance data never goes into git history)
"""
import os
import sys
from datetime import date, timedelta

import psycopg2
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "ingestion"))
from ingest import load_env, require

INK = "1F2937"
HEAD_FILL = PatternFill("solid", fgColor="123A32")
SUB_FILL = PatternFill("solid", fgColor="E8EFEC")
NOTE_FONT = Font(name="Arial", size=9, italic=True, color="5C6B66")
H1 = Font(name="Arial", size=15, bold=True, color=INK)
HEAD_FONT = Font(name="Arial", size=10, bold=True, color="FFFFFF")
BODY = Font(name="Arial", size=10, color=INK)
BODY_B = Font(name="Arial", size=10, bold=True, color=INK)
thin = Side(style="thin", color="C9D2CD")
BORDER = Border(left=thin, right=thin, top=thin, bottom=thin)

HEADS = ["CA", "Auto email", "Manual email", "Emails", "Dials", "Pursuits",
         "Conversations", "LinkedIn", "Other", "Inbound replies",
         "Mtg booked", "Mtg held", "Mtg canceled", "Mtg scheduled", "Mtg unknown",
         "Total", "Accounts touched", "Contacts touched",
         "Accounts owned", "Owned touched", "Coverage %"]

SELECT = """
    select ca_name, auto_email, manual_email, emails, dials, pursuits,
           conversations, linkedin, other_outreach, inbound_replies,
           meetings_booked, meetings_held, meetings_canceled,
           meetings_scheduled, meetings_unknown, total_counted,
           accounts_touched, contacts_touched, accounts_owned, owned_touched
    from rep_scorecard(%s, %s)
"""

TEAM_DISTINCT = """
    select count(distinct company_id) filter (where company_id is not null),
           count(distinct contact_id) filter (where contact_id is not null)
    from activity_flat
    where counts and ca_id is not null and activity_date between %s and %s
"""


def sheet(wb, title, window_label, rows, team_accounts, team_contacts, first=False):
    ws = wb.active if first else wb.create_sheet()
    ws.title = title
    ws.sheet_view.showGridLines = False
    ws["A1"] = f"CA rep scorecard - {window_label}"
    ws["A1"].font = H1
    ws["A2"] = ("Source: rep_scorecard() view in Supabase (Phase 4), read-only over activity_flat. "
                "Definitions: docs/ontology.md. Meetings are BOOKED unless the held column says otherwise.")
    ws["A2"].font = NOTE_FONT

    r = 4
    for i, h in enumerate(HEADS):
        c = ws.cell(row=r, column=1 + i, value=h)
        c.font = HEAD_FONT
        c.fill = HEAD_FILL
        c.border = BORDER
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    r += 1
    r0 = r
    for row in rows:
        name = row[0]
        vals = row[1:]
        cell = ws.cell(row=r, column=1, value=name)
        cell.font = BODY_B
        cell.border = BORDER
        for j, v in enumerate(vals):
            c = ws.cell(row=r, column=2 + j, value=int(v))
            c.font = BODY
            c.border = BORDER
            c.number_format = "#,##0"
            c.alignment = Alignment(horizontal="right")
        # Emails column as a formula over its components (col D = B + C)
        ws.cell(row=r, column=4, value=f"=B{r}+C{r}")
        # Coverage % as a formula over owned/touched (col U = T / S)
        c = ws.cell(row=r, column=21, value=f'=IF(S{r}=0,"n/a",T{r}/S{r})')
        c.font = BODY
        c.border = BORDER
        c.number_format = "0.0%"
        c.alignment = Alignment(horizontal="right")
        r += 1

    # team row
    ws.cell(row=r, column=1, value="All 17 CAs").font = BODY_B
    ws.cell(row=r, column=1).fill = SUB_FILL
    ws.cell(row=r, column=1).border = BORDER
    for col in range(2, 22):
        letter = ws.cell(row=1, column=col).coordinate.rstrip("1")
        c = ws.cell(row=r, column=col, value=f"=SUM({letter}{r0}:{letter}{r-1})")
        c.font = BODY_B
        c.fill = SUB_FILL
        c.border = BORDER
        c.number_format = "#,##0"
        c.alignment = Alignment(horizontal="right")
    # distinct team-wide accounts/contacts are NOT the sum of per-rep values
    # (two reps can touch the same account) - overwrite with true distincts
    ws.cell(row=r, column=17, value=int(team_accounts))
    ws.cell(row=r, column=18, value=int(team_contacts))
    c = ws.cell(row=r, column=21, value=f"=T{r}/S{r}")
    c.font = BODY_B
    c.fill = SUB_FILL
    c.border = BORDER
    c.number_format = "0.0%"
    c.alignment = Alignment(horizontal="right")
    ws.cell(row=r + 2, column=1, value=(
        "Coverage % = of the accounts THIS rep owns (HubSpot target-account owner), the share they touched "
        "in the window. Accounts/Contacts touched skip the ~60% of activities with no matched company - "
        "they understate breadth; watch the trend, not the level. Team accounts/contacts are true distincts, "
        "not the per-rep sum. Pursuits/Conversations are subsets of Dials; Total counts every activity once.")
    ).font = NOTE_FONT
    ws.freeze_panes = "B5"
    ws.column_dimensions["A"].width = 28
    for col in range(2, 22):
        ws.column_dimensions[ws.cell(row=1, column=col).coordinate.rstrip("1")].width = 11
    return ws


def main():
    load_env()
    conn = psycopg2.connect(require("SUPABASE_DB_URL"), connect_timeout=20)
    conn.autocommit = True
    cur = conn.cursor()

    today = date.today()
    windows = [
        ("Last 7 days", today - timedelta(days=7), today - timedelta(days=1)),
        ("All time", date(2026, 7, 6), today - timedelta(days=1)),
    ]

    wb = Workbook()
    first = True
    for label, start, end in windows:
        cur.execute(SELECT, (start, end))
        rows = cur.fetchall()
        cur.execute(TEAM_DISTINCT, (start, end))
        team_accounts, team_contacts = cur.fetchone()
        sheet(wb, label, f"{start} to {end} (UTC)", rows,
              team_accounts or 0, team_contacts or 0, first=first)
        first = False
    conn.close()

    out = os.path.join(_HERE, "..", "reports", f"CA_rep_scorecard_{today}.xlsx")
    out = os.path.abspath(out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    wb.save(out)
    print("saved", out)


if __name__ == "__main__":
    main()
