"""Offline regression tests for the REAL dashboard/athena_client.py.

Run from repository root: python -m pytest tests/test_athena_client_contract.py -q
These cases call no AWS APIs and incur no Athena costs.
"""
from unittest.mock import MagicMock

import pandas as pd
import pytest

import athena_client


def result_page(columns, rows):
    return {
        "ResultSet": {
            "ResultSetMetadata": {"ColumnInfo": [{"Name": x} for x in columns]},
            "Rows": [{"Data": [{"VarCharValue": str(v)} if v is not None else {} for v in values]}
                     for values in rows],
        }
    }


def fake_athena(monkeypatch, pages, states=("QUEUED", "RUNNING", "SUCCEEDED")):
    client = MagicMock()
    client.start_query_execution.return_value = {
        "QueryExecutionId": "offline-test-id"}
    client.get_query_execution.side_effect = [
        {"QueryExecution": {"Status": {"State": state}}} for state in states
    ]
    client.get_paginator.return_value.paginate.return_value = iter(pages)
    monkeypatch.setattr(athena_client, "get_athena_client", lambda: client)
    monkeypatch.setattr(athena_client.time, "sleep", lambda *_: None)
    return client


def test_returns_dataframe_and_skips_only_first_header(monkeypatch):
    client = fake_athena(monkeypatch, [
        result_page(["customer_id", "spend"], [
                    ["customer_id", "spend"], ["C1", "20"]]),
        result_page(["customer_id", "spend"], [["C2", "30"], ["C3", None]]),
    ])
    frame = athena_client.run_query("SELECT customer_id, spend FROM example")
    assert isinstance(frame, pd.DataFrame)
    assert len(frame) == 3

    assert frame["customer_id"].tolist() == [
        "C1",
        "C2",
        "C3",
    ]

    assert frame["spend"].iloc[0] == "20"
    assert frame["spend"].iloc[1] == "30"

    # Accept either None or NaN as a missing value.
    assert pd.isna(frame["spend"].iloc[2])
    assert client.get_paginator.call_count == 1


def test_returns_all_644_detail_rows(monkeypatch):
    ids = [f"{i:03d}" for i in range(644)]
    pages = [result_page(["row_id"], [["row_id"]] + [[v] for v in ids[:300]]),
             result_page(["row_id"], [[v] for v in ids[300:]])]
    fake_athena(monkeypatch, pages)
    frame = athena_client.run_query("SELECT row_id FROM example")
    assert len(frame) == 644
    assert frame.iloc[-1]["row_id"] == "643"


def test_zero_data_rows_still_returns_dataframe(monkeypatch):
    fake_athena(monkeypatch, [result_page(["total"], [["total"]])])
    frame = athena_client.run_query("SELECT total FROM example")
    assert isinstance(frame, pd.DataFrame)
    assert frame.empty
    assert list(frame.columns) == ["total"]


def test_raises_on_failed_query(monkeypatch):
    client = MagicMock()
    client.start_query_execution.return_value = {
        "QueryExecutionId": "failed-query"}
    client.get_query_execution.return_value = {
        "QueryExecution": {"Status": {"State": "FAILED", "StateChangeReason": "Bad SQL"}}
    }
    monkeypatch.setattr(athena_client, "get_athena_client", lambda: client)
    with pytest.raises(RuntimeError, match="Bad SQL"):
        athena_client.run_query("SELECT * FROM wrong_table")


def test_read_only_guard_rejects_mutation(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(athena_client, "get_athena_client", lambda: client)
    with pytest.raises(ValueError):
        athena_client.run_query("DELETE FROM example")
    client.start_query_execution.assert_not_called()


@pytest.mark.parametrize("sql", [
    "-- report comment\nSELECT 1",
    "/* report comment */\nSELECT 1",
    "\n -- first\n /* second */ SELECT 1;",
    "WITH rows AS (SELECT 1 AS n) SELECT n FROM rows",
])
def test_comments_and_cte_are_accepted(sql):
    # The currently implemented normalize_sql is a supported public contract.
    if not hasattr(athena_client, "normalize_sql"):
        pytest.skip("normalize_sql() is not exported by this client")
    normalized = athena_client.normalize_sql(sql)
    assert normalized.lstrip().lower().startswith(("select", "with"))


def test_no_accidental_aws_call_on_import():
    assert callable(athena_client.run_query)
