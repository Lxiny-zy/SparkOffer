"""Auth routes — login, register, config, account management."""
from fastapi import APIRouter, HTTPException, Depends

from backend.models import RegisterRequest, LoginRequest, UpdateProfileRequest, ChangePasswordRequest
from backend.config import settings
from backend.auth import (
    create_user, authenticate_user, create_token, get_current_user,
    update_user_profile, change_user_password, get_user_by_id,
)

router = APIRouter(prefix="/api")


@router.get("/auth/config")
def auth_config():
    return {"allow_registration": settings.allow_registration}


@router.post("/auth/register")
def register(req: RegisterRequest):
    user = create_user(req.email, req.password, req.name)
    token = create_token(user["id"])
    return {"token": token, "user": user}


@router.post("/auth/login")
def login(req: LoginRequest):
    user = authenticate_user(req.email, req.password)
    if not user:
        raise HTTPException(401, "Invalid email or password")
    token = create_token(user["id"])
    return {"token": token, "user": user}


@router.get("/auth/me")
def get_me(user_id: str = Depends(get_current_user)):
    user = get_user_by_id(user_id)
    if not user:
        raise HTTPException(404, "User not found")
    return user


@router.put("/auth/profile")
def update_profile(req: UpdateProfileRequest, user_id: str = Depends(get_current_user)):
    user = update_user_profile(user_id, name=req.name, email=req.email)
    return {"ok": True, "user": user}


@router.put("/auth/password")
def change_password(req: ChangePasswordRequest, user_id: str = Depends(get_current_user)):
    ok = change_user_password(user_id, req.current_password, req.new_password)
    if not ok:
        raise HTTPException(400, "Current password is incorrect")
    return {"ok": True}


@router.get("/")
def root():
    return {"service": "SparkOffer", "version": "0.3.0"}
