# 记忆旅馆 × PowerContext API 契约对照

本文件说明 Dify Chatflow 里两个 HTTP 节点与 PowerContext HTTP API 的对应关系，便于评审在不导入 Dify 的情况下核对请求体是否合法。

## 1. 节点与接口

| Dify 节点 | 方法 / 路径 | 请求体来源 | PowerContext 必填字段 |
| --- | --- | --- | --- |
| 「组装检索请求」→「检索记忆」 | `POST {PC_BASE_URL}/v1/memory/search` | 代码节点 `pack_recall` 生成的 JSON | `scope_id`、`query`；可选 `limit`、`mode` |
| 「组装记忆」→「写入记忆」 | `POST {PC_BASE_URL}/v1/memory/remember` | 代码节点 `pack_remember` 生成的 JSON | `scope_id`、`kind`、`text` |

`pack_recall` 实际输出：

```json
{"scope_id": "{{PC_SCOPE_ID}}", "query": "<用户本轮输入>", "limit": 5, "mode": "fts"}
```

`pack_remember` 实际输出：

```json
{"scope_id": "{{PC_SCOPE_ID}}", "kind": "interaction", "text": "玩家说：……\n阿婆记下：……"}
```

两个字段集合分别与 `openapi/powercontext.yaml` 中的 `SearchMemoryRequest`、`RememberMemoryRequest` 一致（`additionalProperties: false`，因此不能多传字段）。

## 2. 手工验证命令

在 PowerContext 已启动（`http://127.0.0.1:8123`）且 `Scope` 已创建的前提下：

```bash
# 1) 创建 Scope（Dify 环境变量 PC_SCOPE_ID 填返回的 scope_id）
curl -s -X POST http://127.0.0.1:8123/v1/scopes \
  -H 'Content-Type: application/json' \
  -d '{"title":"记忆旅馆","summary":"跨会话记忆","idempotency_key":"memory-inn-demo"}'

# 2) 写入一条记忆（对应「写入记忆」节点）
curl -s -X POST http://127.0.0.1:8123/v1/memory/remember \
  -H 'Content-Type: application/json' \
  -d '{"scope_id":"<scope_id>","kind":"interaction","text":"玩家说：我只喝不加糖的美式"}'

# 3) 检索记忆（对应「检索记忆」节点）
curl -s -X POST http://127.0.0.1:8123/v1/memory/search \
  -H 'Content-Type: application/json' \
  -d '{"scope_id":"<scope_id>","query":"美式","limit":5,"mode":"fts"}'
```

预期：第 2 步返回 `200` 和写入结果；第 3 步返回 `200`，`hits` 中包含刚写入的 `text`。

## 3. 与脚本的关系

- `scripts/init_scope.py`：完成上面的第 1 步，并把 `scope_id` 写入 `.env.powercontext`；
- `scripts/verify_memory.py`：自动执行第 1–3 步并打印每一步的 HTTP 状态与响应，退出码 0 表示接口契约成立；
- `scripts/validate_dsl.py`：检查 DSL 结构、节点连线、环境变量和两个代码节点是否构造了上述必填字段。

## 4. 常见失败

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `422` 且提示 `additionalProperties` | 请求体多传了字段（例如把 SDK 的 `entries` 数组直接发给 HTTP API） | 按上表只传 `scope_id`/`query` 或 `scope_id`/`kind`/`text` |
| `404` 或连接被拒绝 | `PC_BASE_URL` 填错，或 Dify 容器访问不到宿主 | 容器内用 `http://host.docker.internal:8123`，同 compose 用 `http://powercontext:8123` |
| `401` | 服务开启了鉴权 | 设置 `POWERCONTEXT_SERVER_ALLOW_UNAUTHENTICATED_NON_LOOPBACK=true` 或配置 token |
| 检索结果为空 | 记忆还没写入，或 `mode` 与索引不匹配 | 先执行第 2 步，再用 `mode: "fts"` 检索 |
