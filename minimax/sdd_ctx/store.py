"""seekdb-backed material store (server mode, MySQL protocol)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pymysql

from .materials import Material, query_terms

SCHEMA = """
CREATE TABLE IF NOT EXISTS sdd_materials (
  material_id VARCHAR(200) PRIMARY KEY,
  kind VARCHAR(16) NOT NULL,
  path VARCHAR(512) NOT NULL,
  symbol VARCHAR(256) NOT NULL,
  title VARCHAR(512) NOT NULL,
  content TEXT NOT NULL,
  content_hash VARCHAR(64) NOT NULL
)
"""


def settings() -> dict[str, object]:
    return {
        "host": os.environ.get("SEEKDB_HOST", "127.0.0.1"),
        "port": int(os.environ.get("SEEKDB_PORT", "2881")),
        "user": os.environ.get("SEEKDB_USER", "root"),
        "password": os.environ.get("SEEKDB_PASSWORD", ""),
        "database": os.environ.get("SEEKDB_DATABASE", "sdd_ctx"),
        "charset": "utf8mb4",
        "autocommit": True,
    }


def ensure_database() -> str:
    cfg = settings()
    database = cfg.pop("database")
    connection = pymysql.connect(**cfg)
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{database}`")
    finally:
        connection.close()
    return str(database)


class MaterialStore:
    """Materials live in seekdb; retrieval is done by seekdb SQL."""

    def __init__(self) -> None:
        self.settings = settings()
        self.connection = pymysql.connect(**self.settings)
        self._ensure_schema()

    @property
    def backend(self) -> str:
        return f"seekdb {self.settings['host']}:{self.settings['port']}/{self.settings['database']}"

    def _ensure_schema(self) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(SCHEMA)
            for parser in ("WITH PARSER ngram", ""):
                try:
                    cursor.execute(f"CREATE FULLTEXT INDEX idx_materials_content ON sdd_materials(content) {parser}")
                    break
                except Exception:  # noqa: BLE001 - index already exists or parser unsupported
                    continue

    def replace_all(self, materials: list[Material]) -> int:
        with self.connection.cursor() as cursor:
            cursor.execute("DELETE FROM sdd_materials")
            if materials:
                cursor.executemany(
                    "INSERT INTO sdd_materials (material_id, kind, path, symbol, title, content, content_hash) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    [
                        (
                            material.material_id,
                            material.kind,
                            material.path,
                            material.symbol,
                            material.title,
                            material.content,
                            material.content_hash,
                        )
                        for material in materials
                    ],
                )
        return len(materials)

    def count(self) -> int:
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM sdd_materials")
            return int(cursor.fetchone()[0])

    def kinds(self) -> dict[str, int]:
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT kind, COUNT(*) FROM sdd_materials GROUP BY kind")
            return {str(kind): int(count) for kind, count in cursor.fetchall()}

    def search(self, query: str, limit: int = 50) -> list[dict[str, object]]:
        rows = self._search_fulltext(query, limit)
        if rows is None:
            rows = self._search_like(query, limit)
        return rows

    def _search_fulltext(self, query: str, limit: int) -> list[dict[str, object]] | None:
        sql = (
            "SELECT material_id, kind, path, symbol, title, content "
            "FROM sdd_materials WHERE MATCH(content) AGAINST (%s) "
            "ORDER BY MATCH(content) AGAINST (%s) DESC LIMIT %s"
        )
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(sql, (query, query, limit))
                return [
                    {
                        "material_id": row[0],
                        "kind": row[1],
                        "path": row[2],
                        "symbol": row[3],
                        "title": row[4],
                        "content": row[5],
                    }
                    for row in cursor.fetchall()
                ]
        except Exception:  # noqa: BLE001 - fall back to LIKE
            return None

    def _search_like(self, query: str, limit: int) -> list[dict[str, object]]:
        terms = query_terms(query)
        if not terms:
            return []
        where = " OR ".join(["content LIKE %s"] * len(terms))
        sql = (
            f"SELECT material_id, kind, path, symbol, title, content FROM sdd_materials WHERE {where} LIMIT %s"
        )
        with self.connection.cursor() as cursor:
            cursor.execute(sql, (*[f"%{term}%" for term in terms], limit))
            rows = [
                {
                    "material_id": row[0],
                    "kind": row[1],
                    "path": row[2],
                    "symbol": row[3],
                    "title": row[4],
                    "content": row[5],
                }
                for row in cursor.fetchall()
            ]
        return rows


def collect_and_store(root: Path) -> tuple[int, dict[str, int], str]:
    from .materials import collect_materials

    ensure_database()
    store = MaterialStore()
    try:
        count = store.replace_all(collect_materials(root))
        return count, store.kinds(), store.backend
    finally:
        store.connection.close()


__all__ = ["MaterialStore", "ensure_database", "collect_and_store", "settings"]
