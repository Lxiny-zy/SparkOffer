"""Favorites routes."""
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import Response

from backend.storage.favorites import (
    add_favorite as _add_fav, list_favorites as _list_favs,
    update_favorite as _update_fav, delete_favorite as _del_fav,
    get_favorite_tags as _get_fav_tags, export_favorites as _export_favs,
)
from backend.auth import get_current_user

router = APIRouter(prefix="/api")


@router.post("/favorites")
async def create_favorite(body: dict, user_id: str = Depends(get_current_user)):
    return _add_fav(
        user_id=user_id,
        session_id=body.get("session_id"),
        question=body.get("question", ""),
        user_answer=body.get("user_answer", ""),
        reference_answer=body.get("reference_answer", ""),
        score=body.get("score"),
        assessment=body.get("assessment", ""),
        topic=body.get("topic", ""),
        difficulty=body.get("difficulty", ""),
        tags=body.get("tags"),
    )


@router.get("/favorites")
async def list_favorites_endpoint(
    topic: str = None, tag: str = None,
    sort_by: str = "created_at", sort_order: str = "desc",
    limit: int = 50, offset: int = 0,
    user_id: str = Depends(get_current_user),
):
    return _list_favs(
        user_id=user_id, topic=topic, tag=tag,
        sort_by=sort_by, sort_order=sort_order,
        limit=limit, offset=offset,
    )


@router.put("/favorites/{fav_id}")
async def update_favorite_endpoint(fav_id: str, body: dict, user_id: str = Depends(get_current_user)):
    ok = _update_fav(fav_id, user_id=user_id, tags=body.get("tags"), note=body.get("note"))
    if not ok:
        raise HTTPException(404, "Favorite not found.")
    return {"ok": True}


@router.delete("/favorites/{fav_id}")
async def delete_favorite_endpoint(fav_id: str, user_id: str = Depends(get_current_user)):
    ok = _del_fav(fav_id, user_id=user_id)
    if not ok:
        raise HTTPException(404, "Favorite not found.")
    return {"ok": True}


@router.get("/favorites/tags")
async def list_favorite_tags(user_id: str = Depends(get_current_user)):
    return _get_fav_tags(user_id=user_id)


@router.post("/favorites/export")
async def export_favorites_endpoint(body: dict, user_id: str = Depends(get_current_user)):
    fmt = body.get("format", "json")
    content = _export_favs(
        user_id=user_id, ids=body.get("ids"),
        topic=body.get("topic"), fmt=fmt,
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
