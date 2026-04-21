"""Shared in-memory session stores + persistence helpers.

Centralises the four in-memory dicts and the save/get/del helpers
so that every router can import them without circular deps.
"""
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

from backend.storage.live_sessions import (
    save_live_session, load_live_session, delete_live_session,
)

graphs: dict[str, dict] = {}
drill_sessions: dict[str, dict] = {}
job_prep_sessions: dict[str, dict] = {}
algorithm_sessions: dict[str, dict] = {}

_MSG_TYPE_MAP = {"human": HumanMessage, "ai": AIMessage, "system": SystemMessage}


def save_live(store: dict, session_id: str, session_type: str, user_id: str, data: dict):
    store[session_id] = data
    persist_data = dict(data)
    if session_type == "algorithm" and "messages" in persist_data:
        persist_data["messages"] = [
            {"type": getattr(m, "type", "unknown"), "content": m.content}
            for m in persist_data["messages"]
        ]
    save_live_session(session_id, session_type, user_id, persist_data)


def get_live(store: dict, session_id: str, session_type: str):
    if session_id in store:
        return store[session_id]
    data = load_live_session(session_id)
    if data is None:
        return None
    if session_type == "algorithm" and "messages" in data:
        data["messages"] = [
            _MSG_TYPE_MAP.get(m["type"], HumanMessage)(content=m["content"])
            for m in data["messages"]
        ]
    store[session_id] = data
    return data


def del_live(store: dict, session_id: str):
    store.pop(session_id, None)
    delete_live_session(session_id)
