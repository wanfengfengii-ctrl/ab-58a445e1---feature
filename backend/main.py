"""FastAPI 应用: 档案片段重建台。"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .schemas import ValidationError, parse_request, parse_selected_hex
from .solver import plan_isolation, solve

app = FastAPI(title="档案片段重建台", version="1.0.0")


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
    """为 AMBIGUOUS 重建中的选定正文生成证据隔离方案。"""
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
    selected_hex = parse_selected_hex(payload)

    result = solve(length, fragments)
    if result.status != "AMBIGUOUS":
        raise ValidationError(
            [
                {
                    "loc": ["body"],
                    "msg": (
                        f"当前重建裁决为 {result.status}, 仅 AMBIGUOUS(正文歧义) "
                        "结果可发起证据隔离规划"
                    ),
                    "type": "value_error.verdict",
                }
            ]
        )

    candidate_hexes = {b.hex for b in result.bodies}
    if selected_hex not in candidate_hexes:
        options = " / ".join(sorted(candidate_hexes))
        raise ValidationError(
            [
                {
                    "loc": ["body", "selected_hex"],
                    "msg": (
                        f"所选正文 {selected_hex} 不在裁决给出的两份候选正文中"
                        f"(可选: {options})"
                    ),
                    "type": "value_error.selection",
                }
            ]
        )

    plan = plan_isolation(length, fragments, selected_hex)
    return {
        "target_length": length,
        "selected_hex": selected_hex,
        "original_optimal": {
            "total_weight": result.total_weight,
            "fragment_count": result.fragment_count,
        },
        "plan": plan.to_dict(),
    }


# 生产镜像中前端构建产物挂载到根路径; 开发时该目录不存在则跳过。
_STATIC_DIR = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _STATIC_DIR.is_dir():
    app.mount(
        "/",
        StaticFiles(directory=str(_STATIC_DIR), html=True),
        name="frontend",
    )
