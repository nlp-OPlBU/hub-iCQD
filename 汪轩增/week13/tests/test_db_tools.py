from pathlib import Path

import pytest

from sql_agent.db.catalog import SchemaCatalog
from sql_agent.db.executor import SQLExecutionError, SQLExecutor
from sql_agent.db.validator import SQLValidator, normalize_sql


@pytest.fixture(scope="module")
def catalog() -> SchemaCatalog:
    return SchemaCatalog(Path("ref/chinook.db"))


def test_catalog_excludes_sqlite_internal_tables(catalog: SchemaCatalog) -> None:
    assert len(catalog.table_names) == 11
    assert "tracks" in catalog.table_names
    assert "sqlite_sequence" not in catalog.table_names
    assert "sqlite_stat1" not in catalog.table_names


def test_catalog_has_multiple_sample_rows(catalog: SchemaCatalog) -> None:
    profile = catalog._tables["albums"]
    assert profile.row_count == 347
    assert len(profile.sample_rows) == 3


def test_catalog_renders_join_edges(catalog: SchemaCatalog) -> None:
    edges = [edge.render() for edge in catalog.join_edges(["invoice_items", "invoices"])]
    assert "invoice_items.InvoiceId -> invoices.InvoiceId" in edges
    assert catalog.is_connected(["invoice_items", "invoices", "customers"])
    assert not catalog.is_connected(["albums", "customers"])


@pytest.mark.parametrize(
    ("sql", "ok"),
    [
        ("SELECT COUNT(DISTINCT ArtistId) FROM albums;", True),
        ("WITH x AS (SELECT 1 AS n) SELECT * FROM x", True),
        ("SELECT 'DROP' AS word", True),
        ("DROP TABLE tracks", False),
        ("SELECT 1; DROP TABLE tracks", False),
        ("SELECT nope FROM tracks", False),
        ("", False),
    ],
)
def test_validator(sql: str, ok: bool) -> None:
    validator = SQLValidator(Path("ref/chinook.db"))
    result = validator.validate(sql)
    assert result.ok is ok
    if ok:
        assert not result.normalized_sql.endswith(";")
    else:
        assert result.code is not None


def test_validator_does_not_execute() -> None:
    validator = SQLValidator(Path("ref/chinook.db"))
    result = validator.validate("CREATE TABLE should_not_exist(x int)")
    assert not result.ok
    assert result.code.value == "unsafe"


def test_normalize_sql_removes_fence_and_semicolon() -> None:
    assert normalize_sql("```sql\nSELECT 1;\n```") == "SELECT 1"


def test_executor_returns_rows_and_truncates() -> None:
    executor = SQLExecutor(Path("ref/chinook.db"), max_rows=5)
    result = executor.execute("SELECT TrackId, Name FROM tracks ORDER BY TrackId")
    assert result.columns == ["TrackId", "Name"]
    assert result.row_count == 5
    assert result.truncated is True
    assert result.rows[0][1] == "For Those About To Rock (We Salute You)"


def test_executor_is_readonly() -> None:
    executor = SQLExecutor(Path("ref/chinook.db"))
    with pytest.raises(SQLExecutionError):
        executor.execute("CREATE TABLE should_not_exist(x int)")
