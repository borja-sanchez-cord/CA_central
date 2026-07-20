"""Guards the rep-scorecard column layout against silent misalignment.

The bug this prevents: the SQL projection, the spreadsheet headers, and the
in-cell formulas used to be three parallel orderings kept in sync only by
counting — reorder or insert one column and numbers land under the wrong
header with no error. All three now derive from scorecard/layout.COLUMNS;
these tests assert that single source stays internally consistent.

Pure Python — no database, no openpyxl — so it runs in the same CI as the
rule tests.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scorecard"))
import layout


def test_keys_and_headers_unique():
    keys = [c[0] for c in layout.COLUMNS]
    headers = layout.headers()
    assert len(keys) == len(set(keys)), "duplicate column key"
    assert len(headers) == len(set(headers)), "duplicate header"
    assert len(keys) == len(headers)


def test_every_column_has_valid_kind():
    for key, header, kind, refs in layout.COLUMNS:
        assert kind in ("text", "db", "sum", "ratio"), f"{key}: bad kind {kind}"
        if kind in ("sum", "ratio"):
            assert refs and len(refs) == 2, f"{key}: computed column needs 2 refs"
        else:
            assert refs is None, f"{key}: non-computed column must not carry refs"


def test_computed_columns_reference_real_readable_columns():
    """A sum/ratio formula may only point at columns that (a) exist and
    (b) are actually read from SQL (text/db) — never at another formula."""
    readable = set(layout.db_keys())
    all_keys = {c[0] for c in layout.COLUMNS}
    for key, _h, kind, refs in layout.COLUMNS:
        if kind in ("sum", "ratio"):
            for ref in refs:
                assert ref in all_keys, f"{key} references unknown column {ref}"
                assert ref in readable, f"{key} references non-readable column {ref}"


def test_db_keys_are_the_text_and_db_columns_in_order():
    expected = [c[0] for c in layout.COLUMNS if c[2] in ("text", "db")]
    assert layout.db_keys() == expected


def test_select_sql_projects_exactly_the_db_keys():
    sql = layout.select_sql().lower()
    for key in layout.db_keys():
        assert key in sql, f"{key} missing from SELECT"
    # computed columns must NOT be projected (they're built in-sheet)
    for key, _h, kind, _r in layout.COLUMNS:
        if kind in ("sum", "ratio"):
            assert key not in sql, f"computed column {key} should not be in SELECT"


def test_column_letters_match_position():
    assert layout.column_letter(layout.COLUMNS[0][0]) == "A"
    assert layout.column_letter(layout.COLUMNS[25 if len(layout.COLUMNS) > 25 else -1][0])
    # spot-check a couple by known position
    assert layout.column_letter("auto_email") == "B"
    assert layout.column_letter("coverage_pct") == layout.column_letter(layout.COLUMNS[-1][0])


def test_team_distinct_keys_exist_and_are_db_columns():
    for key in layout.TEAM_DISTINCT_KEYS:
        match = [c for c in layout.COLUMNS if c[0] == key]
        assert match, f"TEAM_DISTINCT key {key} not in COLUMNS"
        assert match[0][2] == "db", f"{key} should be a db column to be summable/overridable"
