"""FastAPI 应用：数据中心备用冷却链故障树 HTTP API。"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .fault_tree import FaultTreeError, analyze

app = FastAPI(
    title="数据中心备用冷却链故障树分析 API",
    version="1.0.0",
    description=(
        "接收由基础故障与无环 AND/OR/KOFN 门组成的故障树，"
        "返回顶事件概率、逐项概率降幅（将同一 ID 在所有支路置为不发生）"
        "以及最小割集。所有概率均为约分的分数字符串。"
    ),
)


@app.exception_handler(FaultTreeError)
async def fault_tree_error_handler(_request: Request, exc: FaultTreeError) -> JSONResponse:
    """业务校验失败统一返回 422。"""
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.get("/", tags=["meta"])
@app.get("/health", tags=["meta"])
async def health() -> dict:
    return {"status": "ok"}


@app.post(
    "/analyses",
    status_code=200,
    tags=["analyses"],
    summary="分析故障树",
    responses={
        200: {
            "description": "分析成功",
            "content": {
                "application/json": {
                    "example": {
                        "top_event_probability": "29579367/125000000000000000",
                        "probability_reductions": {
                            "Pow": "29579367/125000000000000000",
                            "P1": "12481467/125000000000000000",
                        },
                        "minimum_cut_set": ["CTS", "E3", "P1", "P2", "Pow"],
                    }
                }
            },
        },
        422: {
            "description": "输入不合法（循环、悬空引用、空门、重复定义或门输入、"
                           "k 越界、概率格式错误、超规模等）",
            "content": {
                "application/json": {"example": {"detail": "故障树存在循环引用：门 'G2' 处于环中"}}
            },
        },
    },
)
async def create_analysis(request: Request) -> dict:
    """执行故障树分析。

    请求体直接透传给分析引擎做结构与语义校验；任何非法输入均返回 422。
    字段含义见 README。
    """
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(status_code=422, content={"detail": "请求体不是合法的 JSON"})
    if not isinstance(payload, dict):
        return JSONResponse(status_code=422, content={"detail": "请求体必须是 JSON 对象"})
    return analyze(payload)
