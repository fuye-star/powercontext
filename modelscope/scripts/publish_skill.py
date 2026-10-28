#!/usr/bin/env python3
"""Package and publish the around-me-explorer skill to ModelScope.

Usage:
    export MODELSCOPE_API_KEY=ms-xxxx
    export MODELSCOPE_OWNER=your-username
    python scripts/publish_skill.py --dry-run          # build the zip only
    python scripts/publish_skill.py                    # upload + create skill

The script follows the two-step ModelScope Skills API:
    1. POST /openapi/v1/files/upload  (multipart, type=skill) -> data.id
    2. POST /openapi/v1/skills        (JSON)                    -> skill record
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path

ENDPOINT = os.environ.get("MODELSCOPE_ENDPOINT", "https://www.modelscope.cn").rstrip("/")
SKILL_DIR = Path(__file__).resolve().parents[1] / "around-me-explorer"
DIST_DIR = Path(__file__).resolve().parents[1] / "dist"


def build_zip(skill_dir: Path, output: Path) -> Path:
    """Zip the skill folder so its root holds exactly one SKILL.md."""

    if not (skill_dir / "SKILL.md").is_file():
        raise SystemExit(f"missing SKILL.md in {skill_dir}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(skill_dir / "SKILL.md", arcname=f"{skill_dir.name}/SKILL.md")
    return output


def _multipart(fields: dict[str, str], file_field: str, file_path: Path) -> tuple[bytes, str]:
    boundary = "----skill" + uuid.uuid4().hex
    chunks: list[bytes] = []
    for key, value in fields.items():
        chunks.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode()
        )
    chunks.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; "
        f"filename=\"{file_path.name}\"\r\nContent-Type: application/zip\r\n\r\n".encode()
    )
    chunks.append(file_path.read_bytes())
    chunks.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def request(method: str, path: str, body: bytes, content_type: str, token: str) -> dict:
    req = urllib.request.Request(
        f"{ENDPOINT}{path}",
        data=body,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": content_type},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")
        raise SystemExit(f"{method} {path} failed: HTTP {error.code} {detail}") from error


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish the skill to ModelScope")
    parser.add_argument("--owner", default=os.environ.get("MODELSCOPE_OWNER", ""))
    parser.add_argument("--name", default="around-me-explorer")
    parser.add_argument("--display-name", default="周边去哪儿：基于高德的地点推荐助手")
    parser.add_argument(
        "--description",
        default=(
            "使用高德 MCP 的周边地点推荐 Skill：解析起点，周边搜索候选，逐个查询步行路线，"
            "按步行时间筛选排序，最多推荐 3 个地点并给出步行距离、预计时间和推荐理由。"
        ),
    )
    parser.add_argument("--category", default="other")
    parser.add_argument("--license", default="MIT License")
    parser.add_argument("--tags", nargs="*", default=["amap", "poi", "walking-route", "recommendation"])
    parser.add_argument("--source-url", default="https://github.com/fuye-star/powercontext")
    parser.add_argument("--skill-dir", default=str(SKILL_DIR))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    zip_path = build_zip(Path(args.skill_dir), DIST_DIR / f"{args.name}.zip")
    size = zip_path.stat().st_size
    print(f"built {zip_path} ({size} bytes)")
    if args.dry_run:
        return 0

    token = os.environ.get("MODELSCOPE_API_KEY", "").strip()
    if not token:
        raise SystemExit("MODELSCOPE_API_KEY is not set")
    if not args.owner:
        raise SystemExit("--owner or MODELSCOPE_OWNER is required")

    body, content_type = _multipart({"type": "skill"}, "file", zip_path)
    uploaded = request("POST", "/openapi/v1/files/upload", body, content_type, token)
    file_id = (uploaded.get("data") or {}).get("id")
    if not file_id:
        raise SystemExit(f"upload response has no data.id: {uploaded}")
    print(f"uploaded zip, skill_file={file_id}")

    payload = {
        "owner": args.owner,
        "skill_name": args.name,
        "display_name": args.display_name,
        "description": args.description,
        "skill_file": file_id,
        "category": args.category,
        "license": args.license,
        "tags": args.tags,
        "source_url": args.source_url,
    }
    created = request(
        "POST",
        "/openapi/v1/skills",
        json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        "application/json",
        token,
    )
    data = created.get("data") or created
    skill_id = data.get("id") or f"@{args.owner}/{args.name}"
    url = f"{ENDPOINT}/skills/{skill_id}"
    print(json.dumps({"skill_id": skill_id, "url": url, "response": data}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
