# 一页技术说明：端侧向量化 + seekdb 混合检索

## 1. 模型与量化

| 项 | 值 |
| --- | --- |
| 模型 | `Xenova/all-MiniLM-L6-v2`（ONNX 版，来自 Hugging Face `Xenova/all-MiniLM-L6-v2`，Apache-2.0） |
| 输出维度 | 384 |
| 运行方式 | Transformers.js v4 + ONNX Runtime Web（浏览器内） |
| 量化方案 | WebGPU：FP16；WASM：INT8（`dtype: "q8"`，ONNX 动态量化 `model_quantized.onnx`） |
| 量化前基线 | `onnx/model.onnx`（FP32） |

模型体积与检索质量以 `npm run bench` 的实测输出为准（`data/bench.json`，Node + ONNX Runtime CPU，同一评测集：8 篇语料、7 条查询，向量检索 top-3 计算 Recall@3 与 MRR）：

| 配置 | 模型文件体积 | 压缩比 | Recall@3 | MRR |
| --- | --- | --- | --- | --- |
| FP32（基线） | 90,387,606 B（≈86.2 MiB） | 1.00× | 0.714（5/7） | 0.620 |
| INT8（q8） | 22,972,370 B（≈21.9 MiB） | **3.93×** | 0.714（5/7，与基线相同） | 0.525 |
| FP16 | 45,297,825 B（≈43.2 MiB） | 2.00× | 与 FP32 同权重精度，浏览器 WebGPU 使用 | — |

同机（Node 24 + ONNX Runtime CPU，`npm run bench`，8 篇语料 / 7 条查询）：

| 指标 | FP32 | INT8 |
| --- | --- | --- |
| 模型加载 | 6.99 s | 1.99 s |
| 8 篇文档向量化 | 53.5 ms | 31.2 ms |
| 7 条查询向量化 | 14.6 ms | 13.1 ms |

> 量化只改变权重精度，不改变 tokenizer 与池化方式；浏览器端与 Node 端使用同一份 ONNX 权重，因此 Node 上测得的精度变化可直接反映浏览器 INT8 的质量。

## 2. 端侧运行与降级

- 页面加载时检测 `navigator.gpu` 并请求 adapter：
  - 可用 → `pipeline(..., { device: "webgpu", dtype: "fp16" })`；
  - 不可用或 WebGPU 初始化抛错 → 自动 `device: "wasm", dtype: "q8"`，并在顶部徽标显示「推理后端：WASM（未检测到 WebGPU）」或「已降级」。
- 文档分块与查询都调用同一个 `feature-extraction` pipeline，`pooling: "mean", normalize: true`，得到 384 维单位向量；模型加载后先用一条 `warmup` 文本预热，再开始向量化。
- 首次加载包含模型下载与编译，页面显示 `load` 与 `warmup` 耗时；后续请求只计推理时间。

## 3. seekdb 使用

| 项 | 值 |
| --- | --- |
| 部署 | `oceanbase/seekdb:latest` 服务端模式，MySQL 协议 2881 |
| 数据库 / 集合 | `stepfun_kb` / `kb_docs` |
| 向量索引 | HNSW，`dimension: 384`，`distance: "cosine"` |
| 全文索引 | `FulltextIndexConfig("space")` |
| 字段 | 文档向量、原文 `documents`、元数据 `title/source/chunk`、主键 `ids` |

写入：浏览器把 `{id,title,text,embedding}` POST 给本地 Node 服务，服务调用
`collection.add({ ids, documents, embeddings, metadatas })`；按批（默认 8 条）写入，每批返回写库耗时，全部完成后立即计数并展示。

检索：服务端只有一次 `collection.hybridSearch({ query: { whereDocument: { $contains } }, knn: { queryEmbeddings: [vec] }, nResults })` 调用：

- 全文条件在 seekdb 内做全文索引过滤；
- `knn.queryEmbeddings` 使用浏览器生成的查询向量，在 seekdb 内做 HNSW 近邻；
- 两路候选由 seekdb 的混合检索（RRF）在库内融合排序，Node 只把结果转成 JSON 返回，不存在“分别取两路再在客户端拼合”的路径。

增量与刷新：每次导入独立调用 `add`，写入完成后新文档立即可检索；`/api/reset` 删除集合并重建；页面实时显示集合中的文档总数，用于确认连续导入和重置后的状态一致。

## 4. 状态与优化

- **集合/会话状态**：集合名、维度、文档数由 `/api/health` 返回；前端每次导入/清空后刷新，避免使用过期计数。
- **批处理**：向量化与写库都按批进行（默认 8，可调 1–64），减少 HTTP 往返和 seekdb 事务开销。
- **模型加载**：只加载一次并复用；加载前先预热，避免首次查询把冷启动时间算进查询延迟。
- **查询路径**：查询只做一次单文本 embedding 和一次 `hybridSearch`，没有额外 rerank 或重复检索。

## 5. 实测结果

环境：Windows 11、Chrome/Edge（WebGPU 可用时）/ Docker Desktop；seekdb `oceanbase/seekdb:latest`（v1.4.0.0）。

| 配置 | 模型体积 | 文档向量化（Node 参考） | seekdb 检索 | 端到端查询延迟 | Recall@3 |
| --- | --- | --- | --- | --- | --- |
| FP32 + WASM（基线） | 86.2 MiB | 53.5 ms / 8 篇 | 约 38 ms（冒烟） | 页面耗时面板 | 0.714 |
| INT8 + WASM | 21.9 MiB | 31.2 ms / 8 篇 | 约 38 ms（冒烟） | 页面耗时面板 | 0.714 |
| FP16 + WebGPU | 43.2 MiB | 在支持 WebGPU 的浏览器上按页面面板记录 | 同上 | 页面耗时面板 | 同权重，与 FP32 一致 |

已有实测记录（本机）：

- 后端冒烟（`node scripts/smoke.mjs`，真实 seekdb）：集合创建成功，写入 2 条文档耗时约 **21 ms**，`hybridSearch` 检索耗时约 **38 ms**；
- 模型体积：`model_quantized.onnx` = **22,972,370 字节**（≈21.9 MiB，HF mirror 实测）；
- Node/CPU 上的完整量化质量与加载/向量化耗时见 `data/bench.json`（`npm run bench` 生成）。

> 浏览器端的 WebGPU/WASM 延迟受机器 GPU、浏览器版本影响，页面顶部与检索面板会直接给出本次运行的实测值；复现时请记录同一设备、同一浏览器版本下的首轮与预热后数据。

## 6. 复现步骤

```bash
docker run -d --name seekdb -p 2881:2881 oceanbase/seekdb
cd stepfun && npm install --legacy-peer-deps
npm run db:init
npm run api      # 终端 1
npm run dev      # 终端 2
# 打开 http://127.0.0.1:5173 → 载入示例语料 → 导入 → 检索
npm run bench    # 生成 data/bench.json（体积 / 量化质量 / 耗时）
```
