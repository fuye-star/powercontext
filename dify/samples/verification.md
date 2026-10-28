# 验证样例：记忆旅馆

本样例验证两件事：

1. Dify 侧的完整流程（检索记忆 → 角色回答 → 写回记忆）能跑通；
2. 记忆确实由 **PowerContext** 存储和检索，新会话能召回上一轮内容。

Dify 节点调用的是下面这组 HTTP 接口，`scripts/verify_memory.py` 用同样的请求复现了它们，因此即使评审暂时没有打开 Dify 界面，也能先确认技术链路。

## 1. 初始条件

- PowerContext Server 已启动（见 README 第 5 节），`/v1/capabilities` 返回 200。
- 执行过 `python scripts/init_scope.py --base-url <地址> --seed`，拿到 `PC_SCOPE_ID`，并填入 Dify 环境变量。
- Dify 应用已导入 `memory-inn.dsl.yml`，模型可用，应用已发布。

## 2. 操作步骤与预期

| 步骤 | 操作 | 预期表现 |
| --- | --- | --- |
| 1 | 新会话，输入：`我叫小满，是个程序员，只喝不加糖的美式。你们家招财猫挺可爱的。` | 阿婆热情招呼，回应你的口味/招财猫，并问你今天来做什么；本轮被写入记忆 |
| 2 | 同一会话继续：`我昨天偷偷把招财猫转了个方向，你别生气啊。` | 阿婆笑着数落你，表示记住了这件事；本轮被写入记忆 |
| 3 | **新开一个会话**，输入：`阿婆，你还记得我吗？` | 阿婆说出至少一个具体细节（小满 / 程序员 / 不加糖的美式 / 招财猫），而不是重新自我介绍 |
| 4 | 对照记忆：`python scripts/verify_memory.py --base-url <地址>` | 脚本用同样接口写入两条记忆并检索回来，输出 PASS |

验收要点：步骤 3 的具体细节必须能在 PowerContext 的检索结果里找到对应原文（步骤 4 的输出即为证据），不能是模型凭空编的。

## 3. 实际运行证据（本机实测）

环境：Windows 11 + Python 3.13，PowerContext Server 以 `--host 0.0.0.0 --port 8126` 运行，SQLite 文件 `_inn_demo.db`。

### 3.1 创建 Scope（init_scope.py）

```text
$ python scripts/init_scope.py --base-url http://127.0.0.1:8126 --key memory-inn-doc-v1 --seed
Scope 已就绪: scp_1a550s11997kfg3zcstt94a827
已写入开张记忆。

把下面两个值填进 Dify 应用「环境变量」：
  PC_BASE_URL = http://127.0.0.1:8126
  PC_SCOPE_ID = scp_1a550s11997kfg3zcstt94a827
```

### 3.2 记忆写入 + 检索闭环（verify_memory.py）

```text
$ python scripts/verify_memory.py --base-url http://127.0.0.1:8126
scope=scp_31dwvy7j70xgmxbc61zmn3tk40
remember ok memory={'family': 'memory', 'artifact_id': 'memory', 'revision': 1} version=mem_ver_12216e767cb8410580e5508b8a6c47e4
remember ok memory={'family': 'memory', 'artifact_id': 'memory', 'revision': 2} version=mem_ver_edb04fe99bc9477f98126e4ad1f8f280
search '程序员 美式' -> ['玩家第一次来店里，说自己是程序员，喜欢喝不加糖的美式。']
search '招财猫 方向' -> ['玩家偷偷把店里的招财猫转了个方向，老板娘假装没看见。']
PASS: scope + remember + search round trip matches the Dify workflow.
```

### 3.3 原始 HTTP 证据（与 Dify 节点同请求）

```text
1) create scope: 201 scp_5pd9seg6vv2tv6bewna8h7g276
2) idempotent scope: 201 True
3) remember: 200 {'family': 'memory', 'artifact_id': 'memory', 'revision': 1} mem_ver_b107d1ec38e24c63b3c5c4bfc65a666a
3) remember: 200 {'family': 'memory', 'artifact_id': 'memory', 'revision': 2} mem_ver_fdac5464f78a4f8382cc7e9e31ff7de2
4) search: 程序员 美式 -> 200 [('玩家第一次来店里，说自己是程序员，喜欢喝不加糖的美式。', ['fts'])]
4) search: 招财猫 方向 -> 200 [('玩家偷偷把店里的招财猫转了个方向，老板娘假装没看见。', ['fts'])]
```

对应的两个请求体（Dify 代码节点生成的就是这两个 JSON）：

```json
POST /v1/memory/search
{"scope_id": "scp_...", "query": "阿婆，你还记得我吗？", "limit": 5, "mode": "fts"}

POST /v1/memory/remember
{"scope_id": "scp_...", "kind": "interaction",
 "text": "玩家说：阿婆，你还记得我吗？\n阿婆记下：哟，是你呀小满——上次你说只喝不加糖的美式，我还记得呢。"}
```

### 3.4 Dify 侧对话样例（预期效果）

```text
用户：我叫小满，是个程序员，只喝不加糖的美式。你们家招财猫挺可爱的。
阿婆：哎哟，程序员好呀，脑子费得多，是得喝点苦的提提神。招财猫啊，它天天替我招财，
      你别看它小。今儿个来住店还是路过呀？

用户：我昨天偷偷把招财猫转了个方向，你别生气啊。
阿婆：好你个小满，我说它怎么脸朝里了呢！行吧行吧，阿婆记下了，
      下回再让我逮着，你可得赔我一杯不加糖的美式。

（新开会话）

用户：阿婆，你还记得我吗？
阿婆：怎么不记得，小满嘛，程序员，只喝不加糖的美式。上回你还把我招财猫转了个方向，
      我可都记在小本本上呢。
```

第三段里的"小满 / 程序员 / 不加糖的美式 / 招财猫"都必须能对应到 3.2 的检索结果，属于"有据可查"的记忆复述。

## 4. 复现说明

- 每次运行 `verify_memory.py` 都会新建一个带时间戳的 Scope，不会污染演示用的 `PC_SCOPE_ID`。
- 想复现"新会话仍记得"：保持 Dify 环境变量里的 `PC_SCOPE_ID` 不变，在 Dify 中新建会话即可；记忆不随会话销毁。
- 想从零开始：删除 PowerContext 的 SQLite 文件并重启服务（或换 `PC_SCOPE_ID`）。
