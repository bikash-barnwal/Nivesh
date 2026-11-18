import pytest

from nivesh.llm import validate_readonly_sql


def test_cte_is_allowed_and_tables_are_restricted():
    assert "WITH" in validate_readonly_sql("WITH x AS (SELECT * FROM stock_daily) SELECT * FROM x", {"stock_daily"})
    with pytest.raises(ValueError):
        validate_readonly_sql("WITH x AS (SELECT * FROM stock_daily) SELECT * FROM secrets", {"stock_daily"})


@pytest.mark.parametrize(
    "query",
    [
        "read_csv('x')",
        "read_text('x')",
        "glob('*.parquet')",
        "SELECT * FROM 'C:/x.parquet'",
        "COPY (SELECT 1) TO 'x'",
        "ATTACH 'x' AS x",
        "SELECT 1; DROP TABLE x",
        "PRAGMA database_list",
    ],
)
def test_unsafe_sql_is_rejected(query):
    with pytest.raises(ValueError):
        validate_readonly_sql(query, {"stock_daily"})
