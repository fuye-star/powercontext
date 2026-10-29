# 一页技术说明：SDD 上下文管理（规格驱动 + MiniMax + seekdb + PowerContext）

## 1. 问题与做法

软件持续演进时，规格、代码和测试分散在不同地方：改一个函数，需要同时知道「哪条规格要求它」「现有实现长什么样」「哪些测试会验证它」。本作品把三者作为**工程资料**统一入库，用一个上下文模块按任务检索出来，交给 MiniMax 完成修改，并把任务进展持久化，形成可复用的闭环：

```text
SPEC.md / 源码 / 测试
      │  import（解析 + 写入）
      ▼
   seekdb（sdd_materials + ngram 全文索引）
      │  context --task（MATCH ... AGAINST 检索 + 按符号/文件名加权）
      ▼
  上下文（适用规格 + 相关代码 + 验证测试 + 历史状态）
      │  minimax（Minimal 生成完整文件）
      ▼
  新代码 → pytest（修改前失败、修改后通过）→ 记录 + PowerContext 验证状态
```

## 2. 规格驱动上下文

- `materials.py` 把 `SPEC.md` 按 `## SPEC-xxx:` 解析成条目；用 `ast` 提取顶层函数/类的源码片段；测试函数单独归类。
- 每个资料条目有稳定 id（`spec:SPEC-002`、`code:expense/filters.py#filter_expenses`、`test:...#test_min_amount_is_inclusive`），便于记录与复核。
- 上下文按任务文本检索后，把命中符号名出现在任务文本里的条目排前，输出 `specs/code/tests/state` 四段结构（JSON 或 Markdown）。

## 3. seekdb 的作用

| 项 | 值 |
| --- | --- |
| 部署 | `oceanbase/seekdb:latest` 服务端模式，MySQL 协议 2881 |
| 表 | `sdd_materials(material_id, kind, path, symbol, title, content, content_hash)` |
| 索引 | `CREATE FULLTEXT INDEX ... WITH PARSER ngram`（中英混合可检索） |
| 检索 | `MATCH(content) AGAINST (%s) ORDER BY MATCH(...) DESC`；不可用时回退 `LIKE` |
| 导入 | 每次 `import` 先清空再批量写入，规格更新后重导即生效 |

`context` 和 `minimax` 命令的候选集全部来自这条 SQL，MiniMax 收到的内容也是检索结果，因此 seekdb 实际决定了「模型看到什么」。

## 4. PowerContext 的作用

- Scope `review-coach`/`sdd-context-state` 中保存两类记忆：`sdd-task-state`（done/todo/verified/时间）和 `sdd-verification`（测试命令、退出码、模型、文件）。
- 每次 CLI 调用都是新进程，`status` 写入、`resume` 读回；`minimax` 结束前把验证结果写回，下一次开发可以接着用。
- 记忆文本是 JSON；读取时反序列化，按 `task_id` 取最近一条。

## 5. MiniMax 的作用与记录

- `minimax_client.py` 调 OpenAI 兼容接口 `POST /v1/chat/completions`，默认 `MiniMax-M3.1-Flash`，Key 只从环境变量读取。
- `agent.py` 把上下文渲染进 messages（规格原文、当前文件、测试清单），要求模型只输出完整文件；落盘前备份原文件到 `records/before/`。
- `records/minimax_session.jsonl` 记录：任务、上下文摘要、完整 messages、模型输出、usage、测试结果；记录中的代码与 `records/after/filters.py` 逐字一致。

## 6. 实测结果

| 阶段 | 结果 |
| --- | --- |
| 修改前 | `4 failed, 2 passed`（缺 `min_amount`/`max_amount` 参数，TypeError） |
| MiniMax 修改后 | `6 passed`（含边界、区间非法抛 ValueError、回归不破） |
| 上下文检索 | `context` 命中 SPEC-002/003/004、`filter_expenses`、4 条目标测试 + 2 条回归测试 |
| 状态复用 | `status` 写入后，`resume` 在新进程读回同一份状态 |

## 7. 复现

```bash
docker run -d --name seekdb -p 2881:2881 oceanbase/seekdb
pip install -e ".[builtin]" && pip install pymysql
export MINIMAX_API_KEY=sk-...
cd minimax
python -m sdd_ctx import
python -m sdd_ctx context --task "实现 min_amount 与 max_amount 区间过滤" --id demo-task
python -m sdd_ctx minimax --id demo-task --task "实现 min_amount 与 max_amount 区间过滤（SPEC-002/003/004）" --file expense/filters.py
python -m sdd_ctx resume --id demo-task
```
