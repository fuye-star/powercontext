"""Behaviour tests for the expense demo project.

The two regression tests already pass before the task; the four target tests fail
until SPEC-002/003/004 are implemented.
"""

from __future__ import annotations

import pytest

from expense.filters import filter_expenses

ROWS = [
    {"id": 1, "category": "Food", "amount": 20},
    {"id": 2, "category": "food", "amount": 80},
    {"id": 3, "category": "Travel", "amount": 120},
    {"id": 4, "category": "Food", "amount": 45},
]


# --- regression: behaviour that must keep working -------------------------


def test_category_filter_is_case_insensitive():
    result = filter_expenses(ROWS, category="FOOD")
    assert [row["id"] for row in result] == [1, 2, 4]


def test_no_filters_returns_copies_and_keeps_order():
    result = filter_expenses(ROWS)
    assert [row["id"] for row in result] == [1, 2, 3, 4]
    result[0]["amount"] = 999
    assert ROWS[0]["amount"] == 20


# --- target: SPEC-002 / SPEC-003 / SPEC-004 -------------------------------


def test_min_amount_is_inclusive():
    result = filter_expenses(ROWS, min_amount=45)
    assert [row["id"] for row in result] == [2, 3, 4]


def test_max_amount_is_inclusive():
    result = filter_expenses(ROWS, max_amount=45)
    assert [row["id"] for row in result] == [1, 4]


def test_combined_bounds_and_category():
    result = filter_expenses(ROWS, category="food", min_amount=20, max_amount=80)
    assert [row["id"] for row in result] == [1, 2, 4]


def test_invalid_range_raises_value_error():
    with pytest.raises(ValueError):
        filter_expenses(ROWS, min_amount=100, max_amount=10)
