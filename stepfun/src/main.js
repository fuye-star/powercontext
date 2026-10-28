import { env, pipeline } from "@huggingface/transformers";

env.allowLocalModels = false;
if (import.meta.env.VITE_HF_ENDPOINT) {
  env.remoteHost = import.meta.env.VITE_HF_ENDPOINT;
}

const MODEL_ID = "Xenova/all-MiniLM-L6-v2";
const DIMENSION = 384;

const state = {
  extractor: null,
  device: "wasm",
  dtype: "q8",
  loadMs: 0,
  warmupMs: 0,
  documents: [],
};

const $ = (id) => document.getElementById(id);
const log = (message) => {
  const node = $("import-log");
  node.textContent = `${new Date().toLocaleTimeString()}  ${message}\n${node.textContent}`;
};

function setBadge(id, text, tone = "") {
  const node = $(id);
  node.textContent = text;
  node.className = `badge ${tone}`.trim();
}

async function detectWebGPU() {
  if (!("gpu" in navigator)) return false;
  try {
    const adapter = await navigator.gpu.requestAdapter();
    return Boolean(adapter);
  } catch {
    return false;
  }
}

async function loadModel() {
  const hasGPU = await detectWebGPU();
  state.device = hasGPU ? "webgpu" : "wasm";
  setBadge(
    "backend-badge",
    hasGPU ? "推理后端：WebGPU" : "推理后端：WASM（未检测到 WebGPU）",
    hasGPU ? "badge-ok" : "badge-warn",
  );

  const candidates = hasGPU
    ? [
        { device: "webgpu", dtype: "fp16" },
        { device: "webgpu", dtype: "fp32" },
        { device: "wasm", dtype: "q8" },
      ]
    : [{ device: "wasm", dtype: "q8" }];

  let lastError = null;
  for (const candidate of candidates) {
    try {
      const started = performance.now();
      const extractor = await pipeline("feature-extraction", MODEL_ID, {
        device: candidate.device,
        dtype: candidate.dtype,
      });
      state.loadMs = performance.now() - started;
      const warm = performance.now();
      await extractor(["warmup"], { pooling: "mean", normalize: true });
      state.warmupMs = performance.now() - warm;
      state.extractor = extractor;
      state.device = candidate.device;
      state.dtype = candidate.dtype;
      setBadge("dtype-badge", `精度：${candidate.dtype.toUpperCase()}`);
      setBadge(
        "model-badge",
        `模型就绪：${(state.loadMs / 1000).toFixed(1)}s`,
        "badge-ok",
      );
      if (hasGPU && candidate.device !== "webgpu") {
        setBadge("backend-badge", "推理后端：WASM（WebGPU 初始化失败，已降级）", "badge-warn");
      }
      log(
        `模型加载完成 device=${candidate.device} dtype=${candidate.dtype} ` +
          `load=${state.loadMs.toFixed(0)}ms warmup=${state.warmupMs.toFixed(0)}ms`,
      );
      return;
    } catch (error) {
      lastError = error;
      log(`加载失败 device=${candidate.device} dtype=${candidate.dtype}: ${error.message}`);
    }
  }
  setBadge("model-badge", "模型加载失败", "badge-warn");
  throw lastError ?? new Error("model load failed");
}

async function embedBatch(texts) {
  const output = await state.extractor(texts, { pooling: "mean", normalize: true });
  return output.tolist();
}

function splitDocuments(raw) {
  const blocks = raw
    .split(/\n\s*---\s*\n/)
    .map((block) => block.trim())
    .filter(Boolean);
  return blocks.map((block, index) => {
    const lines = block.split("\n");
    const title = lines.length > 1 ? lines[0].replace(/^#+\s*/, "") : `文档 ${index + 1}`;
    const text = lines.length > 1 ? lines.slice(1).join("\n").trim() : block;
    return { title, text };
  });
}

function chunkDocument(doc, chunkSize = 420) {
  const sentences = doc.text.split(/(?<=[。！？.!?])\s*|\n+/).filter(Boolean);
  const chunks = [];
  let current = "";
  for (const sentence of sentences) {
    if (current && current.length + sentence.length > chunkSize) {
      chunks.push(current.trim());
      current = "";
    }
    current += sentence;
  }
  if (current.trim()) chunks.push(current.trim());
  return chunks.map((text, index) => ({ title: doc.title, text, chunk: index }));
}

async function refreshHealth() {
  try {
    const response = await fetch("/api/health");
    const data = await response.json();
    if (data.ok) {
      setBadge("model-badge", `模型就绪 · ${(state.loadMs / 1000).toFixed(1)}s`, "badge-ok");
      renderDocuments($("documents"), data.documents);
      return data;
    }
    log(`seekdb 健康检查失败：${data.error}`);
  } catch (error) {
    log(`seekdb 连接失败：${error.message}`);
  }
  return null;
}

function renderDocuments(container, count) {
  container.innerHTML = "";
  const tag = document.createElement("span");
  tag.className = "tag";
  tag.textContent = `seekdb 中已有 ${count} 条文档`;
  container.appendChild(tag);
}

async function importCorpus() {
  const raw = $("corpus-input").value.trim();
  if (!raw) {
    log("请先粘贴或载入语料");
    return;
  }
  if (!state.extractor) {
    log("模型尚未就绪");
    return;
  }
  const docs = splitDocuments(raw).flatMap(chunkDocument);
  const batchSize = Math.max(1, Math.min(64, Number($("batch-size").value) || 8));
  const button = $("import-button");
  button.disabled = true;
  const progress = $("import-progress");
  progress.style.width = "0%";

  const embeddingStarted = performance.now();
  let embedded = 0;
  let writeMs = 0;
  try {
    for (let start = 0; start < docs.length; start += batchSize) {
      const batch = docs.slice(start, start + batchSize);
      const vectors = await embedBatch(batch.map((doc) => doc.text));
      const payload = batch.map((doc, index) => ({
        id: `${doc.title}#${doc.chunk}-${start + index}`,
        title: doc.title,
        text: doc.text,
        embedding: vectors[index],
      }));
      const response = await fetch("/api/docs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ docs: payload }),
      });
      const data = await response.json();
      if (!data.ok) throw new Error(data.error);
      writeMs += data.writeMs ?? 0;
      embedded += batch.length;
      progress.style.width = `${Math.round((embedded / docs.length) * 100)}%`;
      log(`已写入 ${embedded}/${docs.length} 条（本批写入 ${data.writeMs?.toFixed(0)}ms）`);
    }
    const embeddingMs = performance.now() - embeddingStarted - writeMs;
    const health = await refreshHealth();
    log(
      `导入完成：${embedded} 条，向量化 ${embeddingMs.toFixed(0)}ms，` +
        `写库 ${writeMs.toFixed(0)}ms，seekdb 总量 ${health?.documents ?? "?"}`,
    );
  } catch (error) {
    log(`导入失败：${error.message}`);
  } finally {
    button.disabled = false;
  }
}

async function search() {
  const query = $("query-input").value.trim();
  if (!query) return;
  if (!state.extractor) {
    log("模型尚未就绪");
    return;
  }
  const button = $("search-button");
  button.disabled = true;
  const totalStarted = performance.now();
  try {
    const embedStarted = performance.now();
    const [vector] = await embedBatch([query]);
    const embedMs = performance.now() - embedStarted;

    const dbStarted = performance.now();
    const response = await fetch("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ queryText: query, embedding: vector, nResults: 5 }),
    });
    const data = await response.json();
    const roundTripMs = performance.now() - dbStarted;
    if (!data.ok) throw new Error(data.error);

    const totalMs = performance.now() - totalStarted;
    $("timing").textContent =
      `本地查询向量化 ${embedMs.toFixed(0)}ms · seekdb 混合检索 ${data.searchMs.toFixed(0)}ms ` +
      `(HTTP 往返 ${roundTripMs.toFixed(0)}ms) · 端到端 ${totalMs.toFixed(0)}ms · 后端 ${state.device}/${state.dtype}`;

    const list = $("results");
    list.innerHTML = "";
    if (!data.hits.length) {
      const empty = document.createElement("li");
      empty.textContent = "没有命中，试试换一个问法，或先导入更多文档。";
      list.appendChild(empty);
    }
    for (const hit of data.hits) {
      const item = document.createElement("li");
      const title = document.createElement("div");
      title.className = "result-title";
      const name = document.createElement("strong");
      name.textContent = hit.title || hit.id;
      const score = document.createElement("span");
      score.textContent =
        hit.distance === null ? "score —" : `distance ${Number(hit.distance).toFixed(3)}`;
      title.append(name, score);
      const text = document.createElement("p");
      text.className = "result-text";
      text.textContent = hit.text;
      const meta = document.createElement("div");
      meta.className = "result-meta";
      meta.textContent = `source: ${hit.source || "manual"} · id: ${hit.id}`;
      item.append(title, text, meta);
      list.appendChild(item);
    }
  } catch (error) {
    $("timing").textContent = `检索失败：${error.message}`;
  } finally {
    button.disabled = false;
  }
}

function bindEvents() {
  $("import-button").addEventListener("click", importCorpus);
  $("search-button").addEventListener("click", search);
  $("query-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter") search();
  });
  $("file-input").addEventListener("change", async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    const text = await file.text();
    if (file.name.endsWith(".json")) {
      try {
        const docs = JSON.parse(text);
        $("corpus-input").value = docs
          .map((doc) => `${doc.title || doc.id}\n${doc.text}`)
          .join("\n---\n");
        return;
      } catch (error) {
        log(`JSON 解析失败：${error.message}`);
      }
    }
    $("corpus-input").value = text;
  });
  $("sample-button").addEventListener("click", async () => {
    const response = await fetch("/data/corpus.json");
    const docs = await response.json();
    $("corpus-input").value = docs.map((doc) => `${doc.title}\n${doc.text}`).join("\n---\n");
  });
  $("reset-button").addEventListener("click", async () => {
    const response = await fetch("/api/reset", { method: "POST" });
    const data = await response.json();
    log(data.ok ? "已清空 seekdb 集合" : `清空失败：${data.error}`);
    await refreshHealth();
  });
}

async function main() {
  bindEvents();
  await loadModel();
  await refreshHealth();
}

main().catch((error) => log(`启动失败：${error.message}`));
