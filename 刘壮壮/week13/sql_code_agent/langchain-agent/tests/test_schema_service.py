from pathlib import Path

from langchain_sql_agent.schema_service import SchemaService


DB_PATH = Path(__file__).resolve().parents[2] / "data" / "chinook.db"


def test_loads_expected_schema_and_counts() -> None:
    catalog = SchemaService(DB_PATH).load()

    assert len(catalog.tables) == 11
    assert catalog.tables["customers"].row_count == 59
    assert catalog.tables["tracks"].row_count == 3503
    customer_foreign_keys = catalog.tables["customers"].foreign_keys
    assert any(
        key.from_column == "SupportRepId"
        and key.target_table == "employees"
        and key.target_column == "EmployeeId"
        for key in customer_foreign_keys
    )


def test_redacts_sensitive_sample_values() -> None:
    customers = SchemaService(DB_PATH).load().tables["customers"]

    assert customers.samples
    for sample in customers.samples:
        assert sample["Email"] == "[REDACTED]"
        assert sample["Phone"] == "[REDACTED]"
        assert sample["Address"] == "[REDACTED]"
