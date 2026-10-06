"""HTTP API for data-center backup cooling chain fault tree analysis."""

from __future__ import annotations

from typing import Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .engine import FaultTreeError, analyze

app = FastAPI(
    title="Backup Cooling Chain Fault Tree API",
    version="1.0.0",
    description=(
        "Computes the exact top-event probability, the per-basic-fault "
        "probability decrease after forcing each id to not occur, and the "
        "minimum cut set of an AND/OR/k-of-n fault tree."
    ),
)


class BasicFault(BaseModel):
    id: str = Field(..., description="Basic fault id (unique, shared ids are the same fault)")
    probability: str = Field(
        ...,
        description="Failure probability as a decimal string in [0,1], at most 6 decimal places",
    )


class Gate(BaseModel):
    id: str
    type: str = Field(
        ...,
        description="Gate type: AND, OR or KOFN (case-insensitive; 'k_of_n' also accepted)",
        examples=["AND"],
    )
    inputs: list[str] = Field(..., min_length=1, description="Basic fault ids and/or gate ids")
    k: Optional[int] = Field(None, description="Required only for KOFN: 1 <= k <= len(inputs)")


class AnalysisRequest(BaseModel):
    top_event: str
    basic_faults: list[BasicFault]
    gates: list[Gate] = Field(default_factory=list)


class AnalysisResponse(BaseModel):
    top_event_probability: str = Field(..., description="Reduced fraction string, e.g. '3/8'")
    probability_decreases: dict[str, str] = Field(
        ...,
        description=(
            "For every submitted basic fault id: top-event probability decrease "
            "after forcing that id to not occur, as reduced fraction strings"
        ),
    )
    minimum_cut_set: list[str] = Field(
        ...,
        description=(
            "Minimum-cardinality cut set (sorted ids); lexicographically smallest "
            "on ties. Empty if the top event cannot occur."
        ),
    )


@app.exception_handler(FaultTreeError)
async def _fault_tree_error_handler(_request, exc: FaultTreeError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/analyses", response_model=AnalysisResponse)
async def create_analysis(req: AnalysisRequest) -> dict[str, object]:
    return analyze(
        req.top_event,
        [b.model_dump() for b in req.basic_faults],
        [g.model_dump(exclude_none=True) for g in req.gates],
    )
