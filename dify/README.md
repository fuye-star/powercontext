# 记忆旅馆（Memory Inn）—— 一个会记住你的 Dify 小店

> 巷子深处有家老旅店，老板娘阿婆记性极好。你上次说过只喝不加糖的美式，这次进门她就把杯子推过来了；你上次偷偷把招财猫转了个方向，她嘴上说"没看见"，下次见面第一句就提起来。

这是一个用 **Dify Chatflow** 编排、用 **PowerContext** 做跨会话记忆的轻量智能应用：每一次对话都会被阿婆写进记忆库，下一次（哪怕新开会话、换浏览器、隔天再来）她都能检索回来，用角色口吻提起你说的细节。

## 1. 好玩在哪

- **会记事的 NPC**：不是"每次重新自我介绍"的机器人，而是一个真的会翻旧账的老板娘。
- **试探她**：你可以故意改口、否认、或者讲一件糗事，看她记不记得、会不会拆穿你。
- **越聊越熟**：聊得越多，她的记忆里关于你的条目越多，之后能提起的细节也越多。
- **单轮即可体验**：不需要复杂操作，打开应用说一句话就能玩；想验证记忆，新开一个会话再问一句。

## 2. 怎么体验

1. 打开 Dify 里的「记忆旅馆」应用（发布后的聊天页面）。
2. 第一次聊：告诉她你的名字、口味、今天来干什么，或者干一件"坏事"。
3. 在同一个会话里再聊一两句，看她怎么接。
4. **新开一个会话**（或刷新页面、换浏览器），问："阿婆，你还记得我吗？"——她会说出之前的具体细节。
5. 想"重置"她：换一个 `PC_SCOPE_ID`（见第 5、6 节），或删掉 PowerContext 的 SQLite 文件重启服务。

## 3. 技术分工

| 组成 | 实际作用 |
| --- | --- |
| **Dify（必选）** | Chatflow 承担全部流程编排：开始节点接收用户输入 → 组装检索请求 → HTTP 调用 PowerContext 检索记忆 → LLM 以"阿婆"人设生成回答 → 组装记忆 → HTTP 把本轮互动写回 PowerContext → 直接回复。 |
| **PowerContext** | 记忆的真正存储与检索层：按 Scope 保存每次互动（SQLite 持久化），提供全文检索接口；Dify 每次对话前检索、对话后写入。没有它，新会话就记不住任何事。 |

数据流：

```text
用户输入
  └─ 开始节点（sys.query）
      └─ 代码节点「组装检索请求」：把 query + scope_id 编码成 JSON
          └─ HTTP「检索记忆」POST /v1/memory/search  → 命中历史记忆（JSON）
              └─ LLM「阿婆」：人设 + 检索到的记忆 + 本轮输入 → 回答
                  └─ 代码节点「组装记忆」：把本轮对话压缩成一条记忆 JSON
                      └─ HTTP「写入记忆」POST /v1/memory/remember → 持久化
                          └─ 直接回复：输出阿婆的话
```

为什么不是只用 Dify 的会话变量：会话变量只在单个 conversation 内有效，换个会话就没了。PowerContext 把记忆放在应用外部按 Scope 持久化，并支持检索，所以"跨会话记得你"是真的由 PowerContext 完成的，而不是 Dify 变量。

## 4. 运行环境

| 组件 | 版本 / 要求 |
| --- | --- |
| Dify | 1.x（DSL 为 `version: 0.1.5`，建议 1.4 及以上；导出/导入均在 1.x 验证） |
| 模型 | 任意 OpenAI 兼容对话模型。DSL 默认 `langgenius/openai/openai` + `gpt-4o-mini`，需在 Dify 安装 OpenAI 插件并配置 Key；也可以把两个 LLM 节点换成你环境里已配置的模型 |
| PowerContext | Python 3.11/3.12，`powercontext[server]`（本仓库），SQLite 存储，无需 embedding 模型（检索使用 FTS） |
| 网络 | Dify 的 api/worker 容器能够访问 PowerContext 地址（容器内或 `host.docker.internal`） |

DSL 里没有写任何密钥；`PC_BASE_URL`、`PC_SCOPE_ID` 通过 Dify 应用的环境变量传入。

## 5. 部署 PowerContext

### 方案 A：Docker（推荐，跨平台一致）

1. 把 `docker-compose.powercontext.yml` 里的 `powercontext` 服务整段复制进 Dify 的 `docker/docker-compose.yaml`（同一份 compose 会自动共用网络）。
2. 在 Dify 的 `docker/` 目录执行：

   ```bash
   docker compose up -d powercontext
   curl http://127.0.0.1:8123/v1/capabilities
   ```

3. Dify 应用里的 `PC_BASE_URL` 填 `http://powercontext:8123`（容器服务名）。

### 方案 B：本机直接跑（最快验证）

```bash
python -m venv .venv && source .venv/bin/activate
pip install "powercontext[server] @ git+https://github.com/fuye-star/powercontext@2026-competition"

export POWERCONTEXT_SERVER_DATABASE='{"kind":"sqlite","url":"sqlite+aiosqlite:////absolute/path/inn.db"}'
export POWERCONTEXT_SERVER_ALLOW_UNAUTHENTICATED_NON_LOOPBACK=true
powercontext server run --host 0.0.0.0 --port 8123 --no-env-file

# 健康检查
curl http://127.0.0.1:8123/v1/capabilities
```

说明：

- `POWERCONTEXT_SERVER_DATABASE` 用 JSON 形式传入（`kind` + `url`），`sqlite+aiosqlite:////绝对路径/inn.db` 是 SQLite 文件。
- `POWERCONTEXT_SERVER_ALLOW_UNAUTHENTICATED_NON_LOOPBACK=true` 只用于内网/本机演示；对公网暴露时请按 PowerContext 文档启用 Bearer/访问控制，并在 Dify 的 HTTP 节点加 `Authorization` 头。
- 只在本机玩、Dify 也在本机（非容器）时，可以 `--host 127.0.0.1`，`PC_BASE_URL` 用 `http://127.0.0.1:8123`。
- Dify 在 Docker Desktop（Windows/macOS）而 PowerContext 在宿主机时，`PC_BASE_URL` 用 `http://host.docker.internal:8123`（上面的 `--host 0.0.0.0` 是必需的）。

## 6. 导入 DSL 并接线

1. Dify → 应用 → **导入 DSL** → 选择 `memory-inn.dsl.yml`。
2. 若提示缺少插件：安装 `langgenius/openai`（1.0 版以上）。若你不想用 OpenAI，逐个打开「阿婆」等 LLM 节点，把模型换成你环境里已配置的对话模型（节点不需要改其他字段）。
3. 创建记忆 Scope（幂等，重复执行不会重复建）：

   ```bash
   python scripts/init_scope.py --base-url http://127.0.0.1:8123 --seed
   ```

   输出示例：

   ```text
   Scope 已就绪: scp_1a550s11997kfg3zcstt94a827
   已写入开张记忆。

   把下面两个值填进 Dify 应用「环境变量」：
     PC_BASE_URL = http://127.0.0.1:8123
     PC_SCOPE_ID = scp_1a550s11997kfg3zcstt94a827
   ```

4. 回到 Dify 应用 → **环境变量** → 把 `PC_BASE_URL`、`PC_SCOPE_ID` 填成上面的值。
5. **发布**应用，进入聊天页面即可体验。

## 7. 验证样例

初始条件、操作步骤、预期表现和实际运行证据见 [`samples/verification.md`](samples/verification.md)。

快速自检（不依赖 Dify，直接验证 PowerContext 侧的记忆读写，和 Dify 节点调用的是同一组接口）：

```bash
python scripts/verify_memory.py --base-url http://127.0.0.1:8123
```

预期最后一行：`PASS: scope + remember + search round trip matches the Dify workflow.`

## 7.1 提交前自检（无需 Dify 实例）

```bash
# 1) 检查 DSL 结构、节点连线、环境变量、两个代码节点构造的字段
python scripts/validate_dsl.py
# 预期：OK nodes=7 edges=6 env=['PC_BASE_URL', 'PC_SCOPE_ID'] dsl_version=0.1.5

# 2) 对照 PowerContext OpenAPI 核对两个 HTTP 节点的请求体
#    见 samples/api-contract.md（含可直接执行的 curl 命令）
```

## 8. 恢复初始状态

记忆全部在 PowerContext 的 Scope 里，和 Dify 会话无关：

- **想让阿婆"失忆"**：停掉 PowerContext，删除 `inn.db`（方案 B）或 `docker compose down -v`（方案 A，会删数据卷），再重启。
- **想保留数据、换一个玩家**：重新跑 `init_scope.py --key memory-inn-player2`，把新的 `PC_SCOPE_ID` 填进应用（或复制一个应用实例）。
- **只是新开 Dify 会话**：记忆不会清空，这正是作品要展示的能力。

## 9. 已知限制

- 记忆检索使用 FTS（中文按词/字匹配），用户措辞和记忆文本差太远时可能召回不到，表现为"阿婆一时没想起来"；这是检索的真实限制，不是编造。
- 当前一个应用实例对应一个 Scope（单人体验）。多人共用会共享同一个阿婆的记忆；要多玩家隔离，按玩家创建 Scope 即可（`init_scope.py` 支持 `--key`）。
- 记忆按"轮"写入（用户说了什么 + 阿婆回什么，截断到 800 字符），不做长期摘要压缩；聊得特别多时检索结果可能包含较早的条目。
- 演示默认使用内网无鉴权绑定；公网部署需自行加鉴权与 HTTPS。
- LLM 输出的人设稳定性取决于所选模型；prompt 已经约束"只能提记忆里有的细节"，但模型仍可能跑题。

## 10. 文件清单

| 文件 | 用途 |
| --- | --- |
| `memory-inn.dsl.yml` | Dify Chatflow DSL（7 个节点），导入即可用 |
| `scripts/init_scope.py` | 创建/复用记忆 Scope，输出 Dify 环境变量值；`--seed` 写入一条开张记忆 |
| `scripts/verify_memory.py` | 复现 Dify 两个 HTTP 节点的调用，验证 scope → remember → search 闭环 |
| `scripts/validate_dsl.py` | 不依赖 Dify 的 DSL 静态检查：结构、连线、环境变量、HTTP 请求字段 |
| `samples/api-contract.md` | 两个 HTTP 节点与 PowerContext OpenAPI 的字段对照及 curl 验证命令 |
| `docker-compose.powercontext.yml` | 把 PowerContext Server 作为 Dify 同网络服务运行 |
| `Dockerfile.powercontext` | 上述服务的镜像构建文件 |
| `samples/verification.md` | 验证样例：步骤、预期、实际运行日志 |

## 11. 来源与许可说明

- DSL、prompt、脚本均为本作品原创；DSL 结构参照 Dify 公开 Chatflow 的通用节点格式编写，未复制第三方工作流内容。
- 未使用第三方数据集；验证用的两条记忆文本是本作品自己构造的样例。
- `powercontext` 为本仓库（fork）代码，按其仓库许可证使用。
