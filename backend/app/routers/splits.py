from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services import split as svc

router = APIRouter(tags=["拆号"])


class SplitIn(BaseModel):
    source_partition_id: int
    new_part_key: str
    clip_ids: list[int] = Field(default_factory=list)
    title: str | None = None


@router.get("/拆号")
def list_splits():
    return svc.list_splits()


@router.get("/拆号/{split_id}/改挂段")
def list_moves(split_id: int):
    rows = svc.list_moves(split_id)
    if not rows and not any(s["id"] == split_id for s in svc.list_splits()):
        raise HTTPException(404, detail="拆号回执不存在")
    return rows


@router.post("/拆号")
def create(body: SplitIn):
    return svc.split_partition(
        body.source_partition_id,
        body.new_part_key,
        body.clip_ids,
        body.title,
    )
