"""Material parsing: SPEC entries, Python symbols and test functions."""

from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

SPEC_HEADING = re.compile(r"^##\s+(SPEC-\d+)\s*[:：]\s*(.+?)\s*$")

STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "into",
        "must",
        "should",
        "when",
        "then",
        "than",
        "have",
        "has",
        "are",
        "was",
        "were",
        "will",
        "shall",
        "can",
        "not",
        "all",
        "any",
        "each",
        "per",
        "use",
        "using",
        "add",
        "adds",
        "added",
        "implement",
        "实现",
        "并且",
        "必须",
        "不得",
        "返回",
        "条目",
        "以及",
    }
)


@dataclass(frozen=True)
class Material:
    material_id: str
    kind: str
    path: str
    symbol: str
    title: str
    content: str

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()


def parse_specs(root: Path) -> list[Material]:
    materials: list[Material] = []
    spec_path = root / "SPEC.md"
    if not spec_path.exists():
        return materials
    lines = spec_path.read_text(encoding="utf-8").splitlines()
    current: tuple[str, str, list[str]] | None = None
    for line in lines:
        match = SPEC_HEADING.match(line)
        if match:
            if current is not None:
                materials.append(_spec_material(current))
            current = (match.group(1), match.group(2), [])
            continue
        if current is not None:
            current[2].append(line)
    if current is not None:
        materials.append(_spec_material(current))
    return materials


def _spec_material(current: tuple[str, str, list[str]]) -> Material:
    spec_id, title, body = current
    content = f"{spec_id}: {title}\n" + "\n".join(body).strip()
    return Material(
        material_id=f"spec:{spec_id}",
        kind="spec",
        path="SPEC.md",
        symbol=spec_id,
        title=f"{spec_id}: {title}",
        content=content,
    )


def parse_python(root: Path) -> tuple[list[Material], list[Material]]:
    code: list[Material] = []
    tests: list[Material] = []
    for path in sorted(root.rglob("*.py")):
        if ".data" in path.parts or "__pycache__" in path.parts:
            continue
        relative = path.relative_to(root).as_posix()
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (OSError, SyntaxError):
            continue
        is_test = path.name.startswith("test_") or "/tests/" in f"/{relative}"
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbol = node.name
                segment = ast.get_source_segment(source, node) or ""
                title = f"{relative}:{node.lineno} {symbol}"
                material = Material(
                    material_id=f"{'test' if is_test else 'code'}:{relative}#{symbol}",
                    kind="test" if is_test else "code",
                    path=relative,
                    symbol=symbol,
                    title=title,
                    content=f"{relative} :: {symbol}\n{segment}",
                )
                (tests if is_test else code).append(material)
            elif isinstance(node, ast.ClassDef) and not is_test:
                segment = ast.get_source_segment(source, node) or ""
                code.append(
                    Material(
                        material_id=f"code:{relative}#{node.name}",
                        kind="code",
                        path=relative,
                        symbol=node.name,
                        title=f"{relative}:{node.lineno} {node.name}",
                        content=f"{relative} :: {node.name}\n{segment}",
                    )
                )
    return code, tests


def collect_materials(root: Path) -> list[Material]:
    code, tests = parse_python(root)
    return [*parse_specs(root), *code, *tests]


def query_terms(text: str) -> list[str]:
    terms = [token for token in re.findall(r"[a-z0-9_]+", text.casefold()) if len(token) > 1]
    keywords = [term for term in terms if term not in STOPWORDS]
    return list(dict.fromkeys(keywords or terms))
