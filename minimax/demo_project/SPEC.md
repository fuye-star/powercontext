# 费用筛选规格（Demo 业务工程）

本文件是 `demo_project` 的规格来源。上下文模块按 `## SPEC-xxx:` 解析成规格条目。

## SPEC-001: 按类别筛选

`filter_expenses(rows, category=...)` 必须只返回类别等于 `category` 的条目，比较时忽略大小写；`category` 为 `None` 时返回全部条目。

## SPEC-002: 最小金额（含边界）

`filter_expenses(rows, min_amount=...)` 必须保留 `amount >= min_amount` 的条目；`min_amount` 为 `None` 时不做下限过滤。

## SPEC-003: 最大金额（含边界）

`filter_expenses(rows, max_amount=...)` 必须保留 `amount <= max_amount` 的条目；`max_amount` 为 `None` 时不做上限过滤。

## SPEC-004: 区间合法性

当同时给出 `min_amount` 与 `max_amount` 且 `min_amount > max_amount` 时，必须抛出 `ValueError`，不得静默返回空列表。

## SPEC-005: 不修改输入

函数不得修改传入的行对象或列表，返回的新列表中每个元素都是浅拷贝。

