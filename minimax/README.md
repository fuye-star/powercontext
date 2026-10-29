# SDD 上下文管理：规格驱动 + MiniMax 生成 + seekdb/PowerContext

一个面向软件持续演进的最小可运行示例：规格（SPEC）和代码、测试一起存入 **seekdb**，上下文模块按任务检索出**适用规格、相关代码、验证测试**；**MiniMax** 依据这份上下文修改代码；任务进展与验证结果写入 **PowerContext**，重启后仍可读回复用。

## 1. 目录

```text
minimax/
  demo_project/            # 被改造的业务工程
    SPEC.md                # 规格（SPEC-001..005）
    expense/filters.py     # 待实现 min/max 过滤
    tests/test_filters.py  # 2 条回归 + 4 条目标测试
  sdd_ctx/                 # 上下文模块
    materials.py           # 解析 SPEC 条目、Python 符号、测试函数
    store.py               # seekdb（服务端模式）存储 + 全文检索
    state.py               # PowerContext 记忆：任务状态与验证记录
    context.py             # 组装上下文（规格/代码/测试/状态）
    minimax_client.py      # MiniMax OpenAI 兼容 API 客户端
    agent.py               # 用上下文驱动 MiniMax 改代码、跑测试、留记录
    cli.py                 # import/context/status/resume/minimax/tests/reset
  records/
    minimax_session.jsonl  # MiniMax 请求/响应 + 验证记录（真实调用）
    before/filters.py.before
    after/filters.py
    before_tests.log / after_tests.log
```

## 2. 技术分工

| 组成 | 作用 |
| --- | --- |
| **seekdb** | 存 `sdd_materials`（规格/代码/测试），建 ngram 全文索引；`context` 与 `minimax` 命令的检索都由 seekdb `MATCH ... AGAINST` 完成 |
| **PowerContext** | 存任务状态（done/todo/verified）和每次验证结果；`resume` 在新进程读回，供下一次开发复用 |
| **MiniMax** | 读取上下文，生成 `expense/filters.py` 的完整新版本（真实调用，见 `records/minimax_session.jsonl`） |

## 3. 运行环境

| 组件 | 版本 / 要求 |
| --- | --- |
| Python | 3.11+ |
| seekdb | `oceanbase/seekdb:latest`（服务端模式，MySQL 协议 2881） |
| PowerContext | 本仓库源码（`pip install -e ".[builtin]"`） |
| MiniMax | 开放平台 API Key（`MINIMAX_API_KEY`），默认模型 `MiniMax-M3.1-Flash`，默认地址 `https://api.minimaxi.com/v1` |

## 4. 启动 seekdb 与安装

```bash
docker run -d --name seekdb -p 2881:2881 oceanbase/seekdb
docker logs -f seekdb      # 等到 "SeekDB SQL server is accepting connections"

git clone -b 2026-competition-ecosystem https://github.com/fuye-star/powercontext.git
cd powercontext
pip install -e ".[builtin]"
pip install pymysql
```

环境变量（默认值可直接用）：

```bash
export SEEKDB_HOST=127.0.0.1
export SEEKDB_PORT=2881
export SEEKDB_USER=root
export SEEKDB_PASSWORD=
export SEEKDB_DATABASE=sdd_ctx
export MINIMAX_API_KEY=sk-...        # 不提交到仓库
```

## 5. 使用

```bash
cd minimax

# 1) 规格/代码/测试写入 seekdb
python -m sdd_ctx import
# {"imported": 12, "kinds": {"spec": 5, "code": 1, "test": 6}, "backend": "seekdb 127.0.0.1:2881/sdd_ctx"}

# 2) 按任务取上下文（规格 + 代码 + 测试 + 记忆状态）
python -m sdd_ctx context --task "实现 min_amount 与 max_amount 区间过滤（SPEC-002/003/004）" --id demo-task

# 3) 让 MiniMax 依据上下文修改代码（写入记录、跑测试、存验证结果）
python -m sdd_ctx minimax --id demo-task \
  --task "实现 filter_expenses 的 min_amount 与 max_amount 金额区间过滤（含边界），并在 min_amount > max_amount 时抛出 ValueError（SPEC-002/003/004）" \
  --file expense/filters.py

# 4) 记录/读取任务状态（跨进程持久化）
python -m sdd_ctx status --id demo-task --done "实现区间过滤" --todo "补充边界用例" --verified "tests 6 passed"
python -m sdd_ctx resume --id demo-task

# 5) 单独跑测试
python -m sdd_ctx tests
```

## 6. 验证样例（真实运行结果）

### 初始状态

`demo_project/expense/filters.py` 只实现类别过滤；运行测试：

```text
$ python -m pytest -q tests
4 failed, 2 passed
```

失败原因：`filter_expenses() got an unexpected keyword argument 'min_amount'`（缺功能，不是环境错误）。

### MiniMax 修改后

`python -m sdd_ctx minimax ...` 读取 seekdb 上下文（命中 SPEC-002/003/004、`filter_expenses`、4 条目标测试），调用 MiniMax 生成完整文件并落盘，然后自动跑测试：

```text
6 passed
```

`records/minimax_session.jsonl` 保存了完整请求（含规格/代码/测试清单）、MiniMax 返回的代码、模型名、用量和测试结果；`records/before_tests.log` / `after_tests.log` 保存两次测试输出。

### 记忆复用

```text
$ python -m sdd_ctx resume --id demo-task
{
  "schema": "sdd.task-state.v1",
  "task_id": "demo-task",
  "done": ["实现区间过滤"],
  "todo": ["补充边界用例"],
  "verified": ["tests 6 passed"],
  "updated_at": "2026-10-29T..."
}
```

`resume` 是新的 Python 进程，状态由 PowerContext（SQLite 后端）持久化，证明任务状态可跨重启复用。

## 7. MiniMax 开发记录

`records/minimax_session.jsonl` 每行一条：

- `session`：任务、上下文摘要（命中的 spec/code/test id）、发给 MiniMax 的完整 messages、模型返回的代码、finish_reason、usage；
- `verification`：修改后测试命令、before/after 退出码与结果、日志路径。

记录中的代码与 `records/after/filters.py` 一致，可直接核对。

## 8. 说明与限制

- 规格与代码为原创示例；seekdb 使用官方 `oceanbase/seekdb` 镜像，PowerContext 使用本仓库开源实现；
- MiniMax Key 只通过环境变量传入，仓库不包含任何真实凭据；
- 示例的检索使用中英文混合的 ngram 全文索引；规格更新后重新 `import` 即生效（`records` 中可对照导入条数）。
