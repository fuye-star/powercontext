"""seekdb access layer: notes storage + full-text retrieval."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pymysql

SCHEMA_NOTES = """
CREATE TABLE IF NOT EXISTS notes (
  id VARCHAR(64) PRIMARY KEY,
  title VARCHAR(255) NOT NULL,
  topic VARCHAR(64) NOT NULL,
  source VARCHAR(255) NOT NULL,
  content TEXT NOT NULL
)
"""


@dataclass(frozen=True)
class Note:
    id: str
    title: str
    topic: str
    source: str
    content: str


def config() -> dict[str, object]:
    return {
        "host": os.environ.get("SEEKDB_HOST", "127.0.0.1"),
        "port": int(os.environ.get("SEEKDB_PORT", "2881")),
        "user": os.environ.get("SEEKDB_USER", "root"),
        "password": os.environ.get("SEEKDB_PASSWORD", ""),
        "database": os.environ.get("SEEKDB_DATABASE", "review_coach"),
        "charset": "utf8mb4",
        "autocommit": True,
    }


class NoteStore:
    """Notes live in seekdb; retrieval is done by seekdb SQL (full-text + filters)."""

    def __init__(self) -> None:
        self.settings = config()
        self.connection = pymysql.connect(**self.settings)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(SCHEMA_NOTES)
            try:
                cursor.execute("CREATE FULLTEXT INDEX idx_notes_content ON notes(content) WITH PARSER ngram")
            except Exception:  # noqa: BLE001 - already exists
                pass

    def replace_notes(self, notes: list[Note]) -> int:
        with self.connection.cursor() as cursor:
            cursor.execute("DELETE FROM notes")
            cursor.executemany(
                "INSERT INTO notes (id, title, topic, source, content) VALUES (%s, %s, %s, %s, %s)",
                [(note.id, note.title, note.topic, note.source, note.content) for note in notes],
            )
        return len(notes)

    def count(self) -> int:
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM notes")
            return int(cursor.fetchone()[0])

    def topics(self) -> list[tuple[str, int]]:
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT topic, COUNT(*) FROM notes GROUP BY topic ORDER BY topic")
            return [(str(row[0]), int(row[1])) for row in cursor.fetchall()]

    def get(self, note_id: str) -> Note | None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, title, topic, source, content FROM notes WHERE id = %s", (note_id,)
            )
            row = cursor.fetchone()
        return Note(*row) if row else None

    def search(self, query: str, *, limit: int = 6) -> list[tuple[Note, float]]:
        """Rank notes with seekdb full-text search, with a LIKE fallback."""

        with self.connection.cursor() as cursor:
            try:
                cursor.execute(
                    "SELECT id, title, topic, source, content, MATCH(content) AGAINST (%s) AS score "
                    "FROM notes WHERE MATCH(content) AGAINST (%s) ORDER BY score DESC LIMIT %s",
                    (query, query, limit),
                )
                rows = cursor.fetchall()
                if rows:
                    return [(Note(*row[:5]), float(row[5])) for row in rows]
            except Exception:  # noqa: BLE001 - fall back below
                pass

            terms = [term for term in query.replace("？", " ").replace("，", " ").split() if term]
            if not terms:
                terms = [query]
            where = " OR ".join(["content LIKE %s"] * len(terms))
            params = [f"%{term}%" for term in terms]
            cursor.execute(
                f"SELECT id, title, topic, source, content FROM notes WHERE {where} LIMIT %s",
                (*params, limit),
            )
            return [(Note(*row), 0.0) for row in cursor.fetchall()]


def connection_ok() -> tuple[bool, str]:
    try:
        store = NoteStore()
        return True, f"seekdb {store.settings['host']}:{store.settings['port']} database={store.settings['database']} notes={store.count()}"
    except Exception as error:  # noqa: BLE001
        return False, str(error)


def ensure_database() -> str:
    settings = config()
    database = settings.pop("database")
    connection = pymysql.connect(**settings)
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{database}`")
    finally:
        connection.close()
    return str(database)


def parse_note(path: Path) -> Note:
    text = path.read_text(encoding="utf-8-sig")
    title, topic, terms, body = path.stem, "未分类", "", text
    if text.startswith("---"):
        header, _, body = text[3:].partition("\n---\n")
        for line in header.strip().splitlines():
            key, _, value = line.partition(":")
            key, value = key.strip(), value.strip()
            if key == "title":
                title = value
            elif key == "topic":
                topic = value
            elif key == "terms":
                terms = value
    return Note(id=path.stem, title=title, topic=topic, source=f"data/notes/{path.name}", content=body.strip())
