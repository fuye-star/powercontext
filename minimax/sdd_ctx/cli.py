"""Command line entry point for the SDD context module."""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
from pathlib import Path

from .agent import run_pytest
from .context import ContextRequest, build_context, dumps, render_markdown
from .minimax_client import MiniMaxConfig
from .store import MaterialStore, collect_and_store
from .state import TaskStateStore

MINIMAX_DIR = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = MINIMAX_DIR / "demo_project"
DEFAULT_DATA = MINIMAX_DIR / ".data"


async def _import(root: Path, data: Path) -> dict[str, object]:
    del data  # materials live in seekdb server mode; only PowerContext state uses the data dir
    count, kinds, backend = collect_and_store(root)
    return {"imported": count, "kinds": kinds, "backend": backend, "root": str(root)}


async def _context(root: Path, data: Path, task_id: str, task: str) -> dict[str, object]:
    del data
    store = MaterialStore()
    try:
        return await build_context(ContextRequest(task_id=task_id, task=task, root=root), store)
    finally:
        store.connection.close()


def _parse_list(values: list[str] | None) -> tuple[str, ...]:
    items: list[str] = []
    for value in values or ():
        items.extend(part.strip() for part in value.split(",") if part.strip())
    return tuple(items)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sdd_ctx", description="SDD engineering context module")
    sub = parser.add_subparsers(dest="command", required=True)

    import_cmd = sub.add_parser("import", help="import specs, code and tests into seekdb")
    import_cmd.add_argument("--root", default=str(DEFAULT_ROOT))
    import_cmd.add_argument("--data", default=str(DEFAULT_DATA))

    context_cmd = sub.add_parser("context", help="assemble the development context for a task")
    context_cmd.add_argument("--task", required=True)
    context_cmd.add_argument("--id", default="demo-task")
    context_cmd.add_argument("--root", default=str(DEFAULT_ROOT))
    context_cmd.add_argument("--data", default=str(DEFAULT_DATA))
    context_cmd.add_argument("--format", choices=("json", "markdown"), default="json")
    context_cmd.add_argument("--full", action="store_true", help="include full material bodies")

    status_cmd = sub.add_parser("status", help="write task progress into PowerContext")
    status_cmd.add_argument("--id", required=True)
    status_cmd.add_argument("--done", action="append")
    status_cmd.add_argument("--todo", action="append")
    status_cmd.add_argument("--verified", action="append")
    status_cmd.add_argument("--note", default="")
    status_cmd.add_argument("--data", default=str(DEFAULT_DATA))

    resume_cmd = sub.add_parser("resume", help="read saved task progress after a restart")
    resume_cmd.add_argument("--id", required=True)
    resume_cmd.add_argument("--data", default=str(DEFAULT_DATA))

    minimax_cmd = sub.add_parser("minimax", help="ask MiniMax to implement one file from the context")
    minimax_cmd.add_argument("--id", default="demo-task")
    minimax_cmd.add_argument("--task", required=True)
    minimax_cmd.add_argument("--file", required=True, help="file path relative to --root")
    minimax_cmd.add_argument("--root", default=str(DEFAULT_ROOT))
    minimax_cmd.add_argument("--data", default=str(DEFAULT_DATA))
    minimax_cmd.add_argument("--record", default=str(MINIMAX_DIR / "records" / "minimax_session.jsonl"))
    minimax_cmd.add_argument("--dry-run", action="store_true")
    minimax_cmd.add_argument("--no-tests", action="store_true")

    tests_cmd = sub.add_parser("tests", help="run the demo project tests")
    tests_cmd.add_argument("--root", default=str(DEFAULT_ROOT))

    reset_cmd = sub.add_parser("reset", help="delete the local data directory")
    reset_cmd.add_argument("--data", default=str(DEFAULT_DATA))

    args = parser.parse_args(argv)

    if args.command == "import":
        print(json.dumps(asyncio.run(_import(Path(args.root), Path(args.data))), ensure_ascii=False, indent=2))
        return 0

    if args.command == "context":
        context = asyncio.run(_context(Path(args.root), Path(args.data), args.id, args.task))
        if args.format == "markdown":
            print(render_markdown(context, full=args.full))
        else:
            print(dumps(context))
        return 0

    if args.command == "status":
        payload = asyncio.run(
            TaskStateStore(Path(args.data) / "powercontext.db").write(
                args.id,
                done=_parse_list(args.done),
                todo=_parse_list(args.todo),
                verified=_parse_list(args.verified),
                note=args.note,
            )
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.command == "resume":
        state = asyncio.run(TaskStateStore(Path(args.data) / "powercontext.db").read(args.id))
        print(json.dumps(state, ensure_ascii=False, indent=2))
        return 0 if state is not None else 1

    if args.command == "minimax":
        from .agent import run as run_agent

        record = run_agent(
            root=Path(args.root),
            record_path=Path(args.record),
            task_id=args.id,
            task=args.task,
            file_path=args.file,
            config=MiniMaxConfig.from_env(),
            apply=not args.dry_run,
            run_tests=not args.no_tests,
        )
        summary = {
            "model": record["model"],
            "file": record["file"],
            "applied": not args.dry_run,
            "context": record["context_digest"],
            "tests": record.get("tests", {}).get("exit_code"),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    if args.command == "tests":
        result = run_pytest(Path(args.root))
        print(result["stdout"])
        return int(result["exit_code"])

    if args.command == "reset":
        data = Path(args.data)
        if data.exists():
            shutil.rmtree(data)
        print(json.dumps({"removed": str(data)}))
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
