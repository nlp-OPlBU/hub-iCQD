from pathlib import Path

import pytest

from langchain_sql_agent.query_executor import (
    QuerySafetyError,
    ReadOnlyQueryExecutor,
)


DB_PATH = Path(__file__).resolve().parents[2] / "data" / "chinook.db"


def test_returns_columns_rows_and_nulls() -> None:
    result = ReadOnlyQueryExecutor(DB_PATH).execute(
        "SELECT CustomerId, Company FROM customers ORDER BY CustomerId LIMIT 2"
    )

    assert result.columns == ["CustomerId", "Company"]
    assert result.rows[0] == [1, "Embraer - Empresa Brasileira de Aeronáutica S.A."]
    assert result.rows[1] == [2, None]
    assert not result.truncated


def test_empty_result_is_success() -> None:
    result = ReadOnlyQueryExecutor(DB_PATH).execute(
        "SELECT CustomerId FROM customers WHERE Country = 'China'"
    )

    assert result.rows == []
    assert result.row_count == 0


def test_marks_truncated_results() -> None:
    result = ReadOnlyQueryExecutor(DB_PATH, max_result_rows=2).execute(
        "SELECT TrackId FROM tracks ORDER BY TrackId"
    )

    assert result.row_count == 2
    assert result.truncated


def test_authorizer_blocks_writes() -> None:
    with pytest.raises(QuerySafetyError):
        ReadOnlyQueryExecutor(DB_PATH).execute(
            "UPDATE customers SET Company = NULL"
        )
