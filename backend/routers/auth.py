"""Auth routes — login, register, config, account management.

Login/register are rate-limited per client IP (in-memory sliding window) and
write to the audit log. Behind nginx the client IP comes from X-Forwarded-For
(first hop), which frontend/nginx.conf sets from $remote_addr.
"""
from fastapi import APIRouter, HTTPException, Depends, Request

from backend.models import RegisterRequest, LoginRequest, UpdateProfileRequest, ChangePasswordRequest
from backend.config import settings
from backend.auth import (
    create_user, authenticate_user, create_token, get_current_user,
    update_user_profile, change_user_password, get_user_by_id,
    MIN_PASSWORD_LENGTH,
)
from backend import rate_limit
from backend.storage.audit import log_event

router = APIRouter(prefix="/api")

# Sliding-window limits per client IP
_LOGIN_MAX_FAILURES = 5        # per (ip, email) — protects each account
_LOGIN_IP_MAX_FAILURES = 30    # per ip across all emails — slows credential spraying
_LOGIN_WINDOW_S = 900
_REGISTER_MAX_ATTEMPTS = 5     # attempts per window, success or not
_REGISTER_WINDOW_S = 3600


def client_ip(request: Request) -> str:
    # X-Real-IP is unconditionally overwritten by our nginx (frontend/nginx.conf),
    # so it can't be spoofed through the proxy. X-Forwarded-For is append-style
    # (client-supplied values survive in front), so it's only a fallback.
    real = request.headers.get("x-real-ip", "").strip()
    if real:
        return real
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.get("/auth/config")
def auth_config():
    return {
        "allow_registration": settings.allow_registration,
        "invite_required": bool(settings.invite_code),
    }


@router.post("/auth/register")
def register(req: RegisterRequest, request: Request):
    ip = client_ip(request)
    if not rate_limit.check_and_record(f"reg:{ip}", _REGISTER_MAX_ATTEMPTS, _REGISTER_WINDOW_S):
        log_event("register_rate_limited", email=req.email, ip=ip)
        raise HTTPException(429, "Too many registration attempts, try again later")
    try:
        user = create_user(req.email, req.password, req.name, req.invite_code)
    except HTTPException as e:
        log_event("register_blocked", email=req.email, ip=ip, detail={"reason": e.detail})
        raise
    log_event("register_success", user_id=user["id"], email=user["email"], ip=ip)
    token = create_token(user["id"])
    return {"token": token, "user": user}


@router.post("/auth/login")
def login(req: LoginRequest, request: Request):
    ip = client_ip(request)
    email = req.email.lower().strip()
    # Two buckets: (ip, email) so one user's typos can't lock out others behind
    # the same NAT, plus a coarse per-ip cap against credential spraying.
    acct_key = f"login:{ip}:{email}"
    ip_key = f"login-ip:{ip}"
    if not rate_limit.allow(acct_key, _LOGIN_MAX_FAILURES, _LOGIN_WINDOW_S) \
            or not rate_limit.allow(ip_key, _LOGIN_IP_MAX_FAILURES, _LOGIN_WINDOW_S):
        log_event("login_rate_limited", email=email, ip=ip)
        raise HTTPException(429, "Too many failed attempts, try again later")
    user = authenticate_user(email, req.password)
    if not user:
        rate_limit.record_failure(acct_key, _LOGIN_WINDOW_S)
        rate_limit.record_failure(ip_key, _LOGIN_WINDOW_S)
        log_event("login_failed", email=email, ip=ip)
        raise HTTPException(401, "Invalid email or password")
    rate_limit.reset(acct_key)
    log_event("login_success", user_id=user["id"], email=user["email"], ip=ip)
    token = create_token(user["id"])
    return {"token": token, "user": user}


@router.get("/auth/me")
def get_me(user_id: str = Depends(get_current_user)):
    user = get_user_by_id(user_id)
    if not user:
        raise HTTPException(404, "User not found")
    return user


@router.put("/auth/profile")
def update_profile(req: UpdateProfileRequest, request: Request, user_id: str = Depends(get_current_user)):
    user = update_user_profile(user_id, name=req.name, email=req.email)
    log_event("profile_updated", user_id=user_id, ip=client_ip(request),
              detail={"fields": [k for k, v in (("name", req.name), ("email", req.email)) if v is not None]})
    return {"ok": True, "user": user}


@router.put("/auth/password")
def change_password(req: ChangePasswordRequest, request: Request, user_id: str = Depends(get_current_user)):
    if len(req.new_password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(422, f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    ok = change_user_password(user_id, req.current_password, req.new_password)
    if not ok:
        log_event("password_change_failed", user_id=user_id, ip=client_ip(request))
        raise HTTPException(400, "Current password is incorrect")
    log_event("password_changed", user_id=user_id, ip=client_ip(request))
    return {"ok": True}


@router.get("/")
def root():
    return {"service": "SparkOffer", "version": "0.3.0"}
