from pathlib import Path

import pytest

from langchain_sql_agent.sql_safety import SqlSafetyValidator


DB_PATH = Path(__file__).resolve().parents[2] / "data" / "chinook.db"


@pytest.fixture
def validator() -> SqlSafetyValidator:
    return SqlSafetyValidator(DB_PATH)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT COUNT(*) FROM customers",
        "SELECT ';' AS punctuation;",
        "WITH counts AS (SELECT COUNT(*) AS n FROM tracks) SELECT n FROM counts",
        "-- harmless comment\nSELECT Name FROM artists LIMIT 1",
    ],
)
def test_accepts_readonly_queries(
    validator: SqlSafetyValidator, sql: str
) -> None:
    result = validator.validate(sql)

    assert result.is_valid
    assert result.category == "OK"


@pytest.mark.parametrize(
    ("sql", "category"),
    [
        ("DELETE FROM invoices", "SAFETY_VIOLATION"),
        ("UPDATE invoices SET Total = 0", "SAFETY_VIOLATION"),
        ("CREATE TABLE stolen(email TEXT)", "SAFETY_VIOLATION"),
        ("ATTACH DATABASE 'other.db' AS other", "SAFETY_VIOLATION"),
        ("PRAGMA writable_schema = ON", "SAFETY_VIOLATION"),
        ("SELECT load_extension('unsafe')", "SAFETY_VIOLATION"),
        ("SELECT 1; DROP TABLE customers", "MULTI_STATEMENT"),
        (
            "WITH target AS (SELECT 1) DELETE FROM invoices",
            "SAFETY_VIOLATION",
        ),
    ],
)
def test_rejects_unsafe_or_stacked_sql(
    validator: SqlSafetyValidator, sql: str, category: str
) -> None:
    result = validator.validate(sql)

    assert not result.is_valid
    assert result.category == category


def test_classifies_schema_errors(validator: SqlSafetyValidator) -> None:
    result = validator.validate("SELECT MissingColumn FROM customers")

    assert not result.is_valid
    assert result.category == "SCHEMA_ERROR"


def test_rejects_mutating_natural_language_even_if_model_returns_select(
    validator: SqlSafetyValidator,
) -> None:
    result = validator.validate(
        "SELECT * FROM invoices", question="把所有账单删掉。"
    )

    assert result.category == "SAFETY_VIOLATION"
