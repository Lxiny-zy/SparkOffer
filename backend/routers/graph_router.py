"""Question graph route."""
from fastapi import APIRouter, Depends

from backend.graph import build_graph
from backend.auth import get_current_user

router = APIRouter(prefix="/api")


@router.get("/graph/{topic}")
def get_topic_graph(topic: str, user_id: str = Depends(get_current_user)):
    return build_graph(topic, user_id)
