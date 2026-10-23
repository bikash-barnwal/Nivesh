"""Compile the small boolean/arithmetic expressions used in YAML rules into DuckDB SQL.

Rules live in version-controlled YAML (regimes, events, scans). Expressions are tokenised and checked against an
allow-list of column and function names, so a rule can never inject arbitrary SQL.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

_TOKEN = re.compile(r"\s*(?:(\d+(?:\.\d+)?)|([A-Za-z_][A-Za-z0-9_]*)|(<=|>=|!=|==|<>|<|>|=|\+|-|\*|/|\(|\)|,))")
_KEYWORDS = {"and": "AND", "or": "OR", "not": "NOT", "true": "TRUE", "false": "FALSE", "null": "NULL", "is": "IS"}
FUNCTIONS = frozenset({"least", "greatest", "abs", "coalesce", "round", "if"})
_OPERATORS = {"==": "=", "!=": "<>"}


class RuleError(ValueError):
    """Raised for an expression that is malformed or references an unknown name."""


def compile_expr(expr: str, columns: Iterable[str]) -> str:
    allowed = set(columns)
    out: list[str] = []
    pos = 0
    text = expr.strip()
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if match is None or match.end() == pos:
            raise RuleError(f"unexpected character at {pos} in {expr!r}")
        number, ident, op = match.groups()
        pos = match.end()
        if number is not None:
            out.append(number)
        elif op is not None:
            out.append(_OPERATORS.get(op, op))
        else:
            lower = ident.lower()
            if lower in _KEYWORDS:
                out.append(_KEYWORDS[lower])
            elif lower in FUNCTIONS and text[pos:].lstrip().startswith("("):
                out.append(lower)
            elif ident in allowed:
                out.append(f'"{ident}"')
            else:
                raise RuleError(f"unknown name {ident!r} in {expr!r}")
    if not out:
        raise RuleError("empty expression")
    return " ".join(out)
