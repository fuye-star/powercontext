import express from "express";
import {
  FulltextIndexConfig,
  Schema,
  SeekdbClient,
  VectorIndexConfig,
} from "seekdb";

const PORT = Number(process.env.PORT || 8787);
const DATABASE = process.env.SEEKDB_DATABASE || "stepfun_kb";
const COLLECTION = process.env.SEEKDB_COLLECTION || "kb_docs";
const DIMENSION = Number(process.env.EMBEDDING_DIMENSION || 384);

const client = new SeekdbClient({
  host: process.env.SEEKDB_HOST || "127.0.0.1",
  port: Number(process.env.SEEKDB_PORT || 2881),
  user: process.env.SEEKDB_USER || "root",
  password: process.env.SEEKDB_PASSWORD || "",
  database: DATABASE,
});

let collection = null;

async function openCollection() {
  try {
    collection = await client.getCollection({ name: COLLECTION, embeddingFunction: null });
  } catch {
    const schema = new Schema({
      fulltextIndex: new FulltextIndexConfig("space"),
      vectorIndex: new VectorIndexConfig({
        hnsw: { dimension: DIMENSION, distance: "cosine" },
      }),
    });
    collection = await client.createCollection({
      name: COLLECTION,
      schema,
      embeddingFunction: null,
    });
  }
  return collection;
}

const app = express();
app.use(express.json({ limit: "32mb" }));

app.get("/api/health", async (_request, response) => {
  try {
    await openCollection();
    const count = await collection.count();
    response.json({
      ok: true,
      engine: "seekdb",
      collection: COLLECTION,
      dimension: DIMENSION,
      documents: count,
    });
  } catch (error) {
    response.status(500).json({ ok: false, error: String(error?.message || error) });
  }
});

app.post("/api/docs", async (request, response) => {
  try {
    await openCollection();
    const docs = Array.isArray(request.body?.docs) ? request.body.docs : [];
    if (!docs.length) {
      return response.status(400).json({ ok: false, error: "docs is required" });
    }
    const valid = docs.filter(
      (doc) =>
        typeof doc?.id === "string" &&
        typeof doc?.text === "string" &&
        Array.isArray(doc?.embedding) &&
        doc.embedding.length === DIMENSION,
    );
    if (!valid.length) {
      return response.status(400).json({ ok: false, error: "no valid documents" });
    }
    const started = performance.now();
    await collection.add({
      ids: valid.map((doc) => doc.id),
      documents: valid.map((doc) => doc.text),
      embeddings: valid.map((doc) => doc.embedding),
      metadatas: valid.map((doc) => ({
        title: String(doc.title || ""),
        source: String(doc.source || "manual"),
        chunk: Number(doc.chunk || 0),
      })),
    });
    const writeMs = performance.now() - started;
    const count = await collection.count();
    response.json({ ok: true, inserted: valid.length, documents: count, writeMs });
  } catch (error) {
    response.status(500).json({ ok: false, error: String(error?.message || error) });
  }
});

app.post("/api/search", async (request, response) => {
  try {
    await openCollection();
    const { queryText, embedding, nResults } = request.body || {};
    if (typeof queryText !== "string" || !queryText.trim()) {
      return response.status(400).json({ ok: false, error: "queryText is required" });
    }
    if (!Array.isArray(embedding) || embedding.length !== DIMENSION) {
      return response.status(400).json({ ok: false, error: "embedding is required" });
    }
    const limit = Math.min(Math.max(Number(nResults) || 5, 1), 20);
    const started = performance.now();
    // One hybridSearch call: full-text `whereDocument` and vector `knn` are combined
    // and ranked inside seekdb, never fetched separately and merged in Node.
    const result = await collection.hybridSearch({
      query: { whereDocument: { $contains: queryText } },
      knn: { queryEmbeddings: [embedding] },
      nResults: limit,
      include: ["documents", "metadatas", "distances"],
    });
    const searchMs = performance.now() - started;
    const ids = result.ids?.[0] ?? [];
    const documents = result.documents?.[0] ?? [];
    const metadatas = result.metadatas?.[0] ?? [];
    const distances = result.distances?.[0] ?? [];
    const hits = ids.map((id, index) => ({
      id,
      text: documents[index] ?? "",
      title: metadatas[index]?.title ?? "",
      source: metadatas[index]?.source ?? "",
      distance: distances[index] ?? null,
    }));
    response.json({ ok: true, searchMs, hits });
  } catch (error) {
    response.status(500).json({ ok: false, error: String(error?.message || error) });
  }
});

app.post("/api/reset", async (_request, response) => {
  try {
    await client.deleteCollection(COLLECTION).catch(() => {});
    collection = null;
    await openCollection();
    response.json({ ok: true, collection: COLLECTION });
  } catch (error) {
    response.status(500).json({ ok: false, error: String(error?.message || error) });
  }
});

app.listen(PORT, () => {
  console.log(`stepfun-kb api listening on http://127.0.0.1:${PORT}`);
});
