"""FastAPI 应用: 档案片段重建台。"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .planner import plan_isolation
from .schemas import ValidationError, parse_request
from .solver import solve

app = FastAPI(title="档案片段重建台", version="1.1.0")


@app.exception_handler(ValidationError)
async def validation_error_handler(_request: Request, exc: ValidationError) -> JSONResponse:
    # 统一 422 结构: {"detail": [{"loc": [...], "msg": ..., "type": ...}]}
    return JSONResponse(status_code=422, content={"detail": exc.errors})


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/health")
async def api_health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/reconstruct")
async def reconstruct(request: Request) -> dict:
    raw = await request.body()
    if not raw:
        raise ValidationError(
            [{"loc": ["body"], "msg": "请求体为空", "type": "parse_error"}]
        )
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationError(
            [
                {
                    "loc": ["body"],
                    "msg": f"不是合法 JSON: {exc.msg}",
                    "type": "parse_error",
                }
            ]
        )
    length, fragments = parse_request(payload)
    return solve(length, fragments).to_dict()


@app.post("/api/isolate")
async def isolate(request: Request) -> dict:
    """证据隔离规划: 仅在 AMBIGUOUS 时, 对选定候选正文生成隔离方案。"""

    raw = await request.body()
    if not raw:
        raise ValidationError(
            [{"loc": ["body"], "msg": "请求体为空", "type": "parse_error"}]
        )
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationError(
            [
                {
                    "loc": ["body"],
                    "msg": f"不是合法 JSON: {exc.msg}",
                    "type": "parse_error",
                }
            ]
        )
    if not isinstance(payload, dict):
        raise ValidationError(
            [{"loc": ["body"], "msg": "请求体必须是 JSON 对象", "type": "type_error"}]
        )

    length, fragments = parse_request(payload)
    result = solve(length, fragments)

    if "selected_hex" not in payload:
        raise ValidationError(
            [{"loc": ["body", "selected_hex"], "msg": "字段必填", "type": "missing"}]
        )
    raw_selected = payload["selected_hex"]
    if not isinstance(raw_selected, str):
        raise ValidationError(
            [
                {
                    "loc": ["body", "selected_hex"],
                    "msg": "必须是十六进制字符串",
                    "type": "type_error.hex",
                }
            ]
        )

    selected_hex = raw_selected.upper()
    if (
        len(selected_hex) != length * 2
        or any(c not in "0123456789ABCDEF" for c in selected_hex)
    ):
        raise ValidationError(
            [
                {
                    "loc": ["body", "selected_hex"],
                    "msg": f"必须是恰好 {length} 字节({length * 2} 个十六进制字符)"
                    "的偶数长度十六进制串",
                    "type": "value_error.hex",
                }
            ]
        )

    if result.status != "AMBIGUOUS":
        raise ValidationError(
            [
                {
                    "loc": ["body", "selected_hex"],
                    "msg": f"仅在裁决为 AMBIGUOUS 时可规划隔离方案, 当前裁决为 {result.status}",
                    "type": "value_error.plan_context",
                }
            ]
        )

    candidate_hexes = [b.hex for b in result.bodies]
    if selected_hex not in candidate_hexes:
        raise ValidationError(
            [
                {
                    "loc": ["body", "selected_hex"],
                    "msg": "所选正文必须是裁决给出的两份候选正文之一: "
                    f"{candidate_hexes}",
                    "type": "value_error.candidate",
                }
            ]
        )

    plan = plan_isolation(length, fragments, bytes.fromhex(selected_hex))
    return plan.to_dict()


# 生产镜像中前端构建产物挂载到根路径; 开发时该目录不存在则跳过。
_STATIC_DIR = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _STATIC_DIR.is_dir():
    app.mount(
        "/",
        StaticFiles(directory=str(_STATIC_DIR), html=True),
        name="frontend",
    )
