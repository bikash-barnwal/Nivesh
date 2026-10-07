"""Read-only SQL validation and evidence boundary for Copilot requests."""

from __future__ import annotations

import re
from typing import Any

import sqlglot
from sqlglot import exp

from nivesh.evidence import Evidence

_FORBIDDEN = re.compile(
    r"\b(?:COPY|ATTACH|PRAGMA|INSTALL|LOAD|SET)\b|read_csv|read_text|glob\s*\(|information_schema|duckdb_",
    re.IGNORECASE,
)


def validate_readonly_sql(sql: str, allowed_tables: set[str]) -> str:
    if ";" in sql.strip().rstrip(";") or _FORBIDDEN.search(sql):
        raise ValueError("only one read-only query over allowed views is permitted")
    statements = sqlglot.parse(sql, read="duckdb")
    if len(statements) != 1:
        raise ValueError("exactly one SQL statement is required")
    tree = statements[0]
    if not isinstance(tree, (exp.Select, exp.Union, exp.With)) and tree.key not in {"select", "union", "with"}:
        raise ValueError("only read-only SELECT statements are allowed")
    cte_names = {cte.alias_or_name for cte in tree.find_all(exp.CTE)}
    tables = {table.name for table in tree.find_all(exp.Table)}
    unknown = tables - allowed_tables - cte_names
    if unknown:
        raise ValueError(f"disallowed tables: {sorted(unknown)}")
    if any(not isinstance(table.this, exp.Identifier) for table in tree.find_all(exp.Table)):
        raise ValueError("table functions and literal table paths are not allowed")
    return tree.sql(dialect="duckdb")


def answer_with_evidence(answer: str, evidence: Evidence) -> dict[str, Any]:
    return {"answer": answer, "evidence": evidence.as_dict(), "label": "INTERPRETATION"}
