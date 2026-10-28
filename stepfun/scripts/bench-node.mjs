import { readFileSync, writeFileSync } from "node:fs";
import { env, pipeline } from "@huggingface/transformers";

env.remoteHost = process.env.HF_ENDPOINT || "https://hf-mirror.com";

const MODEL_ID = "Xenova/all-MiniLM-L6-v2";
const HOST = process.env.HF_ENDPOINT || "https://hf-mirror.com";
const FILES = {
  fp32: `${HOST}/${MODEL_ID}/resolve/main/onnx/model.onnx`,
  fp16: `${HOST}/${MODEL_ID}/resolve/main/onnx/model_fp16.onnx`,
  q8: `${HOST}/${MODEL_ID}/resolve/main/onnx/model_quantized.onnx`,
};

const corpus = JSON.parse(readFileSync(new URL("../data/corpus.json", import.meta.url), "utf8"));
const queries = JSON.parse(readFileSync(new URL("../data/queries.json", import.meta.url), "utf8"));

async function fileSize(url) {
  const response = await fetch(url, { method: "HEAD", redirect: "follow" });
  if (!response.ok) return null;
  return Number(response.headers.get("content-length") || 0);
}

async function embedAll(extractor, texts) {
  const output = await extractor(texts, { pooling: "mean", normalize: true });
  return output.tolist();
}

function dot(a, b) {
  let sum = 0;
  for (let i = 0; i < a.length; i += 1) sum += a[i] * b[i];
  return sum;
}

function evaluate(vectors, queryVectors) {
  let recall = 0;
  let reciprocal = 0;
  queries.forEach((item, index) => {
    const query = queryVectors[index];
    const ranked = corpus
      .map((doc, docIndex) => ({ id: doc.id, score: dot(query, vectors[docIndex]) }))
      .sort((a, b) => b.score - a.score);
    const position = ranked.findIndex((row) => row.id === item.relevant);
    if (position >= 0 && position < 3) recall += 1;
    if (position >= 0) reciprocal += 1 / (position + 1);
  });
  return { recallAt3: recall / queries.length, mrr: reciprocal / queries.length };
}

async function run(dtype) {
  const started = performance.now();
  const extractor = await pipeline("feature-extraction", MODEL_ID, { device: "cpu", dtype });
  const loadMs = performance.now() - started;
  await extractor(["warmup"], { pooling: "mean", normalize: true });
  const embedStarted = performance.now();
  const docVectors = await embedAll(extractor, corpus.map((doc) => doc.text));
  const docEmbedMs = performance.now() - embedStarted;
  const queryStarted = performance.now();
  const queryVectors = await embedAll(extractor, queries.map((item) => item.query));
  const queryEmbedMs = performance.now() - queryStarted;
  const quality = evaluate(docVectors, queryVectors);
  await extractor.dispose?.();
  return { loadMs, docEmbedMs, queryEmbedMs, ...quality };
}

const sizes = {};
for (const [key, url] of Object.entries(FILES)) sizes[key] = await fileSize(url);

const results = { model: MODEL_ID, sizesBytes: sizes, measuredAt: new Date().toISOString() };
results.fp32 = await run("fp32");
results.q8 = await run("q8");
results.compressionRatio = sizes.fp32 && sizes.q8 ? sizes.fp32 / sizes.q8 : null;

writeFileSync(new URL("../data/bench.json", import.meta.url), JSON.stringify(results, null, 2));
console.log(JSON.stringify(results, null, 2));
