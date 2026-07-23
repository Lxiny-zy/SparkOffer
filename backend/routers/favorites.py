"""Favorites routes."""
from typing import Literal

from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field, model_validator

from backend.storage.favorites import (
    add_favorite as _add_fav, list_favorites as _list_favs,
    update_favorite as _update_fav, delete_favorite as _del_fav,
    get_favorite_tags as _get_fav_tags, export_favorites as _export_favs,
)
from backend.auth import get_current_user

router = APIRouter(prefix="/api")
MAX_EXPORT_ITEMS = 1_000


class FavoriteCreateRequest(BaseModel):
    session_id: str | None = Field(default=None, max_length=200)
    question: str = Field(default="", max_length=100_000)
    user_answer: str = Field(default="", max_length=1_000_000)
    reference_answer: str = Field(default="", max_length=1_000_000)
    score: float | None = Field(default=None, ge=0, le=10)
    assessment: str = Field(default="", max_length=100_000)
    topic: str = Field(default="", max_length=200)
    difficulty: str = Field(default="", max_length=100)
    tags: list[str] | None = Field(default=None, max_length=50)


class FavoriteUpdateRequest(BaseModel):
    tags: list[str] | None = Field(default=None, max_length=50)
    note: str | None = Field(default=None, max_length=1_000_000)

    @model_validator(mode="after")
    def require_change(self):
        if self.tags is None and self.note is None:
            raise ValueError("tags or note is required")
        return self


class FavoriteExportRequest(BaseModel):
    format: Literal["json", "markdown"] = "json"
    ids: list[str] | None = Field(default=None, max_length=MAX_EXPORT_ITEMS)
    topic: str | None = Field(default=None, max_length=200)


@router.post("/favorites")
def create_favorite(
    body: FavoriteCreateRequest,
    user_id: str = Depends(get_current_user),
):
    return _add_fav(
        user_id=user_id,
        session_id=body.session_id,
        question=body.question,
        user_answer=body.user_answer,
        reference_answer=body.reference_answer,
        score=body.score,
        assessment=body.assessment,
        topic=body.topic,
        difficulty=body.difficulty,
        tags=body.tags,
    )


@router.get("/favorites")
def list_favorites_endpoint(
    topic: str = None, tag: str = None,
    sort_by: str = "created_at", sort_order: str = "desc",
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user_id: str = Depends(get_current_user),
):
    return _list_favs(
        user_id=user_id, topic=topic, tag=tag,
        sort_by=sort_by, sort_order=sort_order,
        limit=limit, offset=offset,
    )


@router.put("/favorites/{fav_id}")
def update_favorite_endpoint(
    fav_id: str, body: FavoriteUpdateRequest,
    user_id: str = Depends(get_current_user),
):
    ok = _update_fav(
        fav_id, user_id=user_id, tags=body.tags, note=body.note,
    )
    if not ok:
        raise HTTPException(404, "Favorite not found.")
    return {"ok": True}


@router.delete("/favorites/{fav_id}")
def delete_favorite_endpoint(fav_id: str, user_id: str = Depends(get_current_user)):
    ok = _del_fav(fav_id, user_id=user_id)
    if not ok:
        raise HTTPException(404, "Favorite not found.")
    return {"ok": True}


@router.get("/favorites/tags")
def list_favorite_tags(user_id: str = Depends(get_current_user)):
    return _get_fav_tags(user_id=user_id)


@router.post("/favorites/export")
def export_favorites_endpoint(
    body: FavoriteExportRequest,
    user_id: str = Depends(get_current_user),
):
    fmt = body.format
    if not body.ids:
        total = _list_favs(
            user_id=user_id, topic=body.topic, limit=1, offset=0,
        )["total"]
        if total > MAX_EXPORT_ITEMS:
            raise HTTPException(
                413,
                f"Export contains {total} items; select at most {MAX_EXPORT_ITEMS} at a time.",
            )
    content = _export_favs(
        user_id=user_id, ids=body.ids,
        topic=body.topic, fmt=fmt,
    )
    if fmt == "markdown":
        return Response(
            content=content,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=sparkoffer-favorites.md"},
        )
    return Response(
        content=content,
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=sparkoffer-favorites.json"},
    )
