# 阶跃知识库 · 端侧向量化 + seekdb 混合检索

一个迷你知识库 Web 应用：**文档与查询的向量全部在浏览器本地计算**（Transformers.js + ONNX Runtime Web，优先 WebGPU，自动降级 WASM），向量与原文写入 **seekdb**，检索时由 seekdb 在数据库内完成**向量 + 全文混合召回与融合排序**，前端只负责展示结果。

## 1. 架构

```text
浏览器 (Vite + Transformers.js)
  ├─ 文档分块 → all-MiniLM-L6-v2 本地向量化（WebGPU fp16 / WASM q8）
  ├─ POST /api/docs { id, title, text, embedding }   ← 只上传向量与文本
  └─ POST /api/search { queryText, embedding }       ← 查询向量也在本地生成
        │
Node API (server/index.mjs, Express + seekdb-js)
  ├─ collection.add({ ids, documents, embeddings })  → seekdb 向量索引 + 全文索引
  └─ collection.hybridSearch({ query.whereDocument, knn.queryEmbeddings })
        └─ 全文条件与向量近邻在 seekdb 内一次完成，RRF 融合后返回
```

- **端侧推理**：模型通过 `@huggingface/transformers` 在浏览器加载；`navigator.gpu` 可用时用 `device: "webgpu"` + `dtype: "fp16"`，否则用 `device: "wasm"` + `dtype: "q8"`，界面上明确显示当前后端与精度。
- **seekdb 数据层**：集合 `kb_docs` 同时建立 `FulltextIndexConfig("space")` 与 `VectorIndexConfig({ hnsw: { dimension: 384, distance: "cosine" } })`；`/api/search` 只有一次 `hybridSearch` 调用，不存在"客户端分别取两路再拼合"。
- **状态与刷新**：每次导入按批写入 seekdb，写入完成立即可检索；`/api/reset` 删除并重建集合；页面右上角实时显示集合中的文档数。

## 2. 环境要求

| 组件 | 版本 |
| --- | --- |
| Node.js | 20 或更高（本机验证 24.x） |
| seekdb | `oceanbase/seekdb:latest`（服务端模式，MySQL 协议，默认 2881 端口） |
| 浏览器 | Chrome / Edge 最新版（WebGPU 需要 `chrome://flags/#enable-unsafe-webgpu` 或已开启的安全上下文） |

## 3. 启动步骤

### 3.1 启动 seekdb server

```bash
docker run -d --name seekdb -p 2881:2881 oceanbase/seekdb
# 等待日志出现 "SeekDB SQL server is accepting connections"
docker logs -f seekdb
```

如果本机已经有 `seekdb` 容器在 2881 端口运行，直接复用即可。

### 3.2 安装依赖并初始化数据库

> `seekdb` npm 包 1.4.0 的 peer 依赖 `@seekdb/default-embed` 误把 `workspace:*` 发布出来，
> 必须加 `--legacy-peer-deps`，否则 `npm install` 会报 `EUNSUPPORTEDPROTOCOL`。

```bash
cd stepfun
npm install --legacy-peer-deps
npm run db:init          # 创建 stepfun_kb 数据库，校验 seekdb 连通性
```

### 3.3 启动后端与前端

```bash
# 终端 1：API（默认 http://127.0.0.1:8787）
npm run api

# 终端 2：前端（Vite，默认 http://127.0.0.1:5173，/api 代理到 8787）
npm run dev
```

浏览器打开 <http://127.0.0.1:5173>。

### 3.4 使用

1. 点击「载入示例语料」或粘贴/上传自己的文本（多篇用一行 `---` 分隔，首行为标题），可以选择批大小；
2. 点击「本地向量化并写入 seekdb」，观察进度、向量化耗时、写库耗时与 seekdb 文档总数；
3. 在检索框输入问题，查看结果列表（标题、distance、原文、来源 id）以及
   「本地查询向量化 / seekdb 混合检索 / 端到端」三段耗时和当前推理后端；
4. 「清空知识库」会删除集合并重建，便于重复验证。

## 4. 复现测试与基准

```bash
# 后端冒烟测试（写入两条向量并做一次混合检索）
npm run api            # 终端 1
node scripts/smoke.mjs # 终端 2

# 模型体积 / 量化质量基准（Node/ONNX Runtime CPU，作为参考测量）
npm run bench          # 输出 data/bench.json
```

- `data/bench.json`：模型文件体积（fp32 / fp16 / int8）、压缩比、两种精度的 Recall@3 与 MRR、加载与向量化耗时；
- 浏览器端的实测数据（WebGPU vs WASM、首轮 vs 预热后）由页面上的耗时面板直接读取，测试记录见 `TECH_NOTE.md`。

## 5. 集合与索引

| 项 | 值 |
| --- | --- |
| 数据库 | `stepfun_kb`（可用 `SEEKDB_DATABASE` 覆盖） |
| 集合 | `kb_docs`（可用 `SEEKDB_COLLECTION` 覆盖） |
| 向量 | 384 维，HNSW，cosine 距离 |
| 全文 | `FulltextIndexConfig("space")` |
| 元数据 | `title`、`source`、`chunk` |
| 写入 | `collection.add({ ids, documents, embeddings, metadatas })`，按批 8 条（可调） |
| 检索 | `collection.hybridSearch({ query: { whereDocument: { $contains: text } }, knn: { queryEmbeddings: [vec] }, nResults })` |

后端连接参数可用环境变量覆盖：`SEEKDB_HOST`、`SEEKDB_PORT`、`SEEKDB_USER`、`SEEKDB_PASSWORD`、`SEEKDB_DATABASE`、`SEEKDB_COLLECTION`、`EMBEDDING_DIMENSION`、`PORT`。

## 6. 已知限制

- 示例语料只有 8 篇，用于演示流程；索引结构与代码不依赖语料规模。
- WebGPU 在无 GPU 或浏览器不支持时自动降级 WASM，界面上会显示降级提示；WASM 下速度明显更慢。
- 向量由浏览器计算后经 HTTP 传给本地 Node 服务，Node 只做 seekdb 读写，不做推理；若部署到远程，请自行加鉴权和 HTTPS。
- 中文全文检索使用 `space` 分词，连续中文句子的召回以向量为主、全文为辅；换成 IK 分词需要 seekdb 侧安装对应 parser。

## 7. 目录

```text
stepfun/
  index.html, src/main.js, src/style.css, vite.config.js
  server/index.mjs              # Express + seekdb-js
  scripts/db-init.mjs           # 创建数据库
  scripts/smoke.mjs             # 后端冒烟测试
  scripts/bench-node.mjs        # 模型体积 / 量化质量基准
  data/corpus.json              # 示例语料（8 篇）
  data/queries.json             # 评测查询（7 条，含 gold id）
  data/bench.json               # 基准输出（生成）
  TECH_NOTE.md                  # 一页技术说明
```
