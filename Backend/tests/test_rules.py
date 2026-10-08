import pytest

from nivesh.rules import RuleError, compile_expr

COLS = ["close", "sma20", "stage", "idx_below_sma50"]


def test_compiles_comparisons_and_booleans():
    sql = compile_expr("close > sma20 and not idx_below_sma50 and stage == 2", COLS)
    assert sql == '"close" > "sma20" AND NOT "idx_below_sma50" AND "stage" = 2'


def test_functions_and_arithmetic():
    sql = compile_expr("70 + least(30, close * 10)", COLS)
    assert sql == '70 + least ( 30 , "close" * 10 )'


def test_null_checks():
    assert compile_expr("stage is not null", COLS) == '"stage" IS NOT NULL'


@pytest.mark.parametrize(
    "expr",
    [
        "close > unknown_col",  # unknown name
        "close > 1; DROP TABLE x",  # statement separator
        "close > 1 -- comment",  # comment
        "(select 1)",  # keyword-ish identifier not allow-listed
        "read_csv('x')",  # non-allow-listed function
        "close > 'a'",  # string literal
        "",  # empty
    ],
)
def test_rejects_anything_outside_the_allow_list(expr):
    with pytest.raises(RuleError):
        compile_expr(expr, COLS)


def test_function_name_is_not_a_column_when_not_called():
    with pytest.raises(RuleError):
        compile_expr("least > 1", COLS)
