const BASE = process.env.API_BASE || "http://127.0.0.1:8787";
const DIM = 384;

function vector(seed) {
  let value = seed;
  const out = new Array(DIM);
  for (let i = 0; i < DIM; i += 1) {
    value = (value * 1103515245 + 12345) % 2147483648;
    out[i] = value / 2147483648;
  }
  const norm = Math.sqrt(out.reduce((sum, item) => sum + item * item, 0));
  return out.map((item) => item / norm);
}

async function call(path, options) {
  const response = await fetch(`${BASE}${path}`, options);
  const data = await response.json();
  if (!data.ok) throw new Error(`${path}: ${data.error}`);
  return data;
}

const health = await call("/api/health");
console.log("health", health);

const docs = [
  { id: "smoke-1", title: "seekdb 向量检索", text: "seekdb 支持向量和全文混合检索", embedding: vector(1) },
  { id: "smoke-2", title: "浏览器推理", text: "WebGPU 不可用时降级到 WASM", embedding: vector(2) },
];
const added = await call("/api/docs", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ docs }),
});
console.log("added", added);

const result = await call("/api/search", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ queryText: "seekdb 混合检索", embedding: vector(1), nResults: 5 }),
});
console.log("searchMs", result.searchMs.toFixed(2), "hits", result.hits.map((hit) => hit.id));
const final = await call("/api/health");
console.log("documents", final.documents);
