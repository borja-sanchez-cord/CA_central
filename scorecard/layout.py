"""Single source of truth for the rep-scorecard column layout.

The column ORDER here defines three things at once — the SQL projection read
from `rep_scorecard()`, the spreadsheet headers, and the formula references —
so they can never drift apart (the maintainability risk flagged in review:
before this, the header list, the SELECT, and hardcoded cell letters like
'B'/'S'/'T' were three parallel orderings kept aligned only by counting).

Deliberately dependency-free (no openpyxl, no psycopg2) so it can be unit
tested with no database — see tests/test_scorecard_layout.py.

Each column is (key, header, kind, refs):
  kind "text"  — text value straight from the query (the rep name)
  kind "db"    — integer value straight from rep_scorecard()
  kind "sum"   — spreadsheet formula: refs[0] + refs[1]
  kind "ratio" — spreadsheet formula: refs[0] / refs[1] as a %, blank-guarded
Only "text"/"db" columns are read from SQL; "sum"/"ratio" are computed in-sheet.
"""

COLUMNS = [
    ("ca_name",            "CA",               "text",  None),
    ("auto_email",         "Auto email",       "db",    None),
    ("manual_email",       "Manual email",     "db",    None),
    ("emails",             "Emails",           "sum",   ("auto_email", "manual_email")),
    ("dials",              "Dials",            "db",    None),
    ("pursuits",           "Pursuits",         "db",    None),
    ("conversations",      "Conversations",    "db",    None),
    ("linkedin",           "LinkedIn",         "db",    None),
    ("other_outreach",     "Other",            "db",    None),
    ("inbound_replies",    "Inbound replies",  "db",    None),
    ("meetings_booked",    "Mtg booked",       "db",    None),
    ("meetings_held",      "Mtg held",         "db",    None),
    ("meetings_canceled",  "Mtg canceled",     "db",    None),
    ("meetings_scheduled", "Mtg scheduled",    "db",    None),
    ("meetings_unknown",   "Mtg unknown",      "db",    None),
    ("total_counted",      "Total",            "db",    None),
    ("accounts_touched",   "Accounts touched", "db",    None),
    ("contacts_touched",   "Contacts touched", "db",    None),
    ("accounts_owned",     "Accounts owned",   "db",    None),
    ("owned_touched",      "Owned touched",    "db",    None),
    ("coverage_pct",       "Coverage %",       "ratio", ("owned_touched", "accounts_owned")),
]

# Team totals row: these two are TRUE DISTINCTS across all reps (two reps can
# touch the same account), so they are overwritten, not summed.
TEAM_DISTINCT_KEYS = ("accounts_touched", "contacts_touched")


def db_keys():
    """Keys read from SQL, in order — drives both the SELECT and row reading."""
    return [k for k, _h, kind, _r in COLUMNS if kind in ("text", "db")]


def headers():
    return [h for _k, h, _kind, _r in COLUMNS]


def column_letter(key):
    """A1-style column letter for a key (1-based position in COLUMNS)."""
    idx = [k for k, *_ in COLUMNS].index(key) + 1
    letters = ""
    while idx:
        idx, rem = divmod(idx - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def select_sql(func_call="rep_scorecard(%s, %s)"):
    return "select " + ", ".join(db_keys()) + f"\n    from {func_call}"
