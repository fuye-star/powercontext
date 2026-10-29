# 验证样例（按步骤复现）

环境：Windows 11 / Python 3.13、Docker `oceanbase/seekdb:latest`（v1.4.0.0）监听 `127.0.0.1:2881`、PowerContext（本仓库，SQLite 后端）、MiniMax `MiniMax-M3.1-Flash`。

## 0. 初始状态

- `demo_project/expense/filters.py` 只实现 `category` 过滤；
- `demo_project/SPEC.md` 有 SPEC-001..005；
- seekdb 中还没有资料（首次 `import` 前 `sdd_materials` 为空）。

## 1. 导入规格、代码、测试到 seekdb

```text
$ python -m sdd_ctx import
{
  "imported": 12,
  "kinds": {"spec": 5, "code": 1, "test": 6},
  "backend": "seekdb 127.0.0.1:2881/sdd_ctx",
  "root": "...\\minimax\\demo_project"
}
```

## 2. 按任务检索上下文

```text
$ python -m sdd_ctx context --task "实现 min_amount 与 max_amount 金额区间过滤（SPEC-002/003/004）" --id demo-task
```

输出（节选）：

```json
{
  "task": {"id": "demo-task", "text": "实现 min_amount 与 max_amount 金额区间过滤（SPEC-002/003/004）"},
  "specs": [
    {"id": "SPEC-002", "source": "SPEC.md#SPEC-002", "content": "SPEC-002: 最小金额（含边界）\n`filter_expenses(rows, min_amount=...)` 必须保留 `amount >= min_amount` 的条目……"},
    {"id": "SPEC-004", "source": "SPEC.md#SPEC-004", "content": "SPEC-004: 区间合法性\n当同时给出 min_amount 与 max_amount 且 min_amount > max_amount 时，必须抛出 ValueError……"}
  ],
  "code": [{"symbol": "filter_expenses", "source": "expense/filters.py#filter_expenses"}],
  "tests": [
    {"symbol": "test_min_amount_is_inclusive"},
    {"symbol": "test_max_amount_is_inclusive"},
    {"symbol": "test_combined_bounds_and_category"},
    {"symbol": "test_invalid_range_raises_value_error"},
    {"symbol": "test_category_filter_is_case_insensitive"},
    {"symbol": "test_no_filters_returns_copies_and_keeps_order"}
  ]
}
```

说明：检索命中由 seekdb `MATCH(content) AGAINST (...)` 完成；规格按命中相关性排序，代码/测试按符号加权。

## 3. 修改前测试（目标测试失败，回归通过）

```text
$ cd demo_project && python -m pytest -q tests
FAILED tests/test_filters.py::test_min_amount_is_inclusive - TypeError: filter_expenses() got an unexpected keyword argument 'min_amount'
FAILED tests/test_filters.py::test_max_amount_is_inclusive - TypeError: ...
FAILED tests/test_filters.py::test_combined_bounds_and_category - TypeError: ...
FAILED tests/test_filters.py::test_invalid_range_raises_value_error - TypeError: ...
4 failed, 2 passed
```

完整输出：`records/before_tests.log`。

## 4. MiniMax 依据上下文修改代码

```text
$ python -m sdd_ctx minimax --id demo-task --task "实现 filter_expenses 的 min_amount 与 max_amount 金额区间过滤（含边界），并在 min_amount > max_amount 时抛出 ValueError（SPEC-002/003/004）" --file expense/filters.py
```

过程：检索上下文（命中 5 条规格、`filter_expenses`、6 条测试）→ 调 MiniMax 生成完整文件 → 备份原文件到 `records/before/` → 落盘 `expense/filters.py` → 跑测试 → 写 PowerContext 验证记录 → 追加 `records/minimax_session.jsonl`。

结果：

```text
6 passed
```

完整输出：`records/after_tests.log`；模型返回代码：`records/after/filters.py`。

## 5. 修改后测试

```text
$ cd demo_project && python -m pytest -q tests
......                                                                   [100%]
6 passed in 0.04s
```

目标测试由失败转为通过，两条回归测试保持通过。

## 6. 规格更新后重新导入（上下文随之变化）

把 SPEC-002 追加一句「金额为 0 时也必须保留」，重新执行 `python -m sdd_ctx import`，再检索：

```text
$ python -c "from sdd_ctx.store import MaterialStore; s=MaterialStore(); print([h['material_id'] for h in s.search('金额为 0 时也必须保留')])"
['spec:SPEC-002', 'spec:SPEC-003', 'spec:SPEC-004', 'spec:SPEC-001']
```

新内容可被检索到，说明刷新后的规格会进入下一次上下文（验证后已恢复原 SPEC 并重新导入）。

## 7. 任务状态跨进程复用（PowerContext）

```text
$ python -m sdd_ctx status --id demo-task --done "MiniMax 实现 min/max 区间过滤" --todo "补充边界用例" --verified "pytest: 6 passed"
$ python -m sdd_ctx resume --id demo-task
{
  "schema": "sdd.task-state.v1",
  "task_id": "demo-task",
  "done": ["MiniMax 实现 min/max 区间过滤"],
  "todo": ["补充边界用例"],
  "verified": ["pytest: 6 passed"],
  "updated_at": "2026-10-29T03:25:07+00:00"
}
```

`status` 与 `resume` 是两个独立进程，状态由 PowerContext 持久化。

## 8. 恢复初始状态

```bash
# 恢复代码
cp records/before/filters.py.before demo_project/expense/filters.py
# 清空 seekdb 资料表
python -m sdd_ctx import   # 或直接 DROP TABLE sdd_materials
# 清空 PowerContext 状态
rm -rf .data
```
