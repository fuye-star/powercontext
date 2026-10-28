"""Command line interface for the review coach."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from .db import Note, NoteStore, ensure_database, parse_note
from .memory import Memory

ROOT = Path(__file__).resolve().parents[1]
NOTES_DIR = ROOT / "data" / "notes"
MEMORY_DB = ROOT / "data" / "powercontext.db"


def load_notes(directory: Path = NOTES_DIR) -> list[Note]:
    return [parse_note(path) for path in sorted(directory.glob("*.md"))]


def weak_topics(memory: Memory, user: str) -> set[str]:
    return {str(row["topic"]) for row in memory.profile(user)["weak_topics"]}


def cmd_init(args: argparse.Namespace) -> int:
    database = ensure_database()
    store = NoteStore()
    notes = load_notes()
    store.replace_notes(notes)
    print(json.dumps({"database": database, "notes": len(notes), "store": store.settings["host"]}, ensure_ascii=False))
    return 0


def cmd_notes(args: argparse.Namespace) -> int:
    store = NoteStore()
    for note in sorted(load_notes(), key=lambda item: item.id):
        print(f"{note.id}\t{note.topic}\t{note.title}")
    print(f"合计 {store.count()} 条，主题：{store.topics()}")
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    store = NoteStore()
    memory = Memory(MEMORY_DB)
    weak = weak_topics(memory, args.user)
    results = store.search(args.query)
    if not results:
        print("没有找到相关笔记，试试更具体的关键词。")
        return 1
    ranked = sorted(
        results,
        key=lambda item: (item[0].topic in weak, item[1]),
        reverse=True,
    )[: args.limit]
    memory.record_query(args.user, args.query, [note.id for note, _ in ranked])
    print(f"查询：{args.query}")
    if weak:
        print(f"记忆中的薄弱主题：{'、'.join(sorted(weak))}")
    for note, score in ranked:
        boost = "  ← 薄弱主题，优先复习" if note.topic in weak else ""
        snippet = note.content.replace("\n", " ")[:110]
        print(f"\n[{note.id}] {note.title}（{note.topic}，score={score:.3f}）{boost}")
        print(f"  {snippet}…\n  来源：{note.source}")
    return 0


def extract_term(content: str) -> str | None:
    candidates = re.findall(r"[A-Z][A-Za-z0-9\-]{2,}", content)
    candidates = [term for term in candidates if term.lower() not in {"http", "https"}]
    return candidates[0] if candidates else None


def cmd_quiz(args: argparse.Namespace) -> int:
    store = NoteStore()
    memory = Memory(MEMORY_DB)
    weak = weak_topics(memory, args.user)
    topic = args.topic
    if not topic and weak:
        topic = sorted(weak)[0]
    results = store.search(topic or "复习", limit=20)
    candidates = [note for note, _ in results if not topic or note.topic == topic]
    if not candidates:
        print("没有可出题的笔记。")
        return 1
    note = candidates[args.index % len(candidates)]
    term = extract_term(note.content)
    if term is None:
        print(f"[{note.id}] {note.title}（{note.topic}）没有可挖空的术语，换一条试试。")
        return 1
    question = note.content.replace(term, "____", 1)
    print(f"出题范围：{topic or '全部'}（来自 PowerContext 的薄弱主题）" if weak else f"出题范围：{topic or '全部'}")
    print(f"\n[{note.id}] {note.title}（{note.topic}）")
    print(f"\n{question}\n")
    print(f"答案：{term}（复习后执行 review {args.user} {note.id} --correct/--wrong）")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    store = NoteStore()
    note = store.get(args.note_id)
    if note is None:
        print(f"没有找到笔记 {args.note_id}")
        return 1
    correct = not args.wrong
    memory = Memory(MEMORY_DB)
    memory.record_attempt(args.user, note_id=note.id, topic=note.topic, correct=correct)
    profile = memory.profile(args.user)
    row = next(item for item in profile["topics"] if item["topic"] == note.topic)
    print(
        json.dumps(
            {
                "user": args.user,
                "note": note.id,
                "topic": note.topic,
                "correct": correct,
                "topic_accuracy": row["accuracy"],
                "weak_topics": [item["topic"] for item in profile["weak_topics"]],
            },
            ensure_ascii=False,
        )
    )
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    store = NoteStore()
    memory = Memory(MEMORY_DB)
    profile = memory.profile(args.user)
    profile["seekdb_notes"] = store.count()
    print(json.dumps(profile, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="coach", description="越用越懂你的复习教练")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create seekdb database and import notes").set_defaults(func=cmd_init)
    sub.add_parser("notes", help="list notes stored in seekdb").set_defaults(func=cmd_notes)

    ask = sub.add_parser("ask", help="search notes with seekdb full-text retrieval")
    ask.add_argument("user")
    ask.add_argument("query")
    ask.add_argument("--limit", type=int, default=4)
    ask.set_defaults(func=cmd_ask)

    quiz = sub.add_parser("quiz", help="generate a cloze question from a weak topic")
    quiz.add_argument("user")
    quiz.add_argument("--topic", default="")
    quiz.add_argument("--index", type=int, default=0)
    quiz.set_defaults(func=cmd_quiz)

    review = sub.add_parser("review", help="record a review result into PowerContext")
    review.add_argument("user")
    review.add_argument("note_id")
    review.add_argument("--correct", action="store_true")
    review.add_argument("--wrong", action="store_true")
    review.set_defaults(func=cmd_review)

    stats = sub.add_parser("stats", help="show the memory profile after a restart")
    stats.add_argument("user")
    stats.set_defaults(func=cmd_stats)

    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
