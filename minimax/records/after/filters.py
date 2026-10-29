"""Expense filtering helpers for the SDD demo project."""

from __future__ import annotations

from collections.abc import Iterable, Mapping


def filter_expenses(
    rows: Iterable[Mapping[str, object]],
    *,
    category: str | None = None,
    min_amount: float | None = None,
    max_amount: float | None = None,
) -> list[dict[str, object]]:
    """Return expense rows filtered by category and inclusive amount bounds."""

    if (
        min_amount is not None
        and max_amount is not None
        and min_amount > max_amount
    ):
        raise ValueError("min_amount must not be greater than max_amount")

    filtered: list[dict[str, object]] = []
    for row in rows:
        if category is not None:
            value = str(row.get("category", ""))
            if value.casefold() != category.casefold():
                continue

        amount = row.get("amount")
        if min_amount is not None and (amount is None or amount < min_amount):
            continue
        if max_amount is not None and (amount is None or amount > max_amount):
            continue

        filtered.append(dict(row))
    return filtered
