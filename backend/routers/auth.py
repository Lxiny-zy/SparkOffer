"""Auth routes — login, register, config, account management.

Login/register are rate-limited per client IP (in-memory sliding window) and
write to the audit log. Forwarding headers are accepted only from explicitly
configured trusted proxy networks.
"""
import hashlib
import ipaddress

from fastapi import APIRouter, HTTPException, Depends, Request

from backend.models import RegisterRequest, LoginRequest, UpdateProfileRequest, ChangePasswordRequest
from backend.config import settings
from backend.auth import (
    create_user, authenticate_user, create_token, get_current_user,
    update_user_profile, change_user_password, get_user_by_id,
    validate_new_password,
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


def _public_user(user: dict) -> dict:
    """Remove internal authentication metadata from API responses."""
    return {key: value for key, value in user.items() if not key.startswith("_")}


def client_ip(request: Request) -> str:
    peer_host = request.client.host if request.client else "unknown"
    try:
        peer_ip = ipaddress.ip_address(peer_host)
    except ValueError:
        return peer_host

    try:
        trusted_networks = settings.trusted_proxy_networks()
        trusted = any(peer_ip in network for network in trusted_networks)
    except ValueError:
        # Startup validation normally catches this. Staying on the direct peer
        # is the secure behavior if this helper is invoked independently.
        trusted = False
    if not trusted:
        return str(peer_ip)

    # Walk X-Forwarded-For from the server side of the chain. Selecting the
    # left-most value directly lets a client prepend a spoofed address when a
    # trusted proxy appends instead of replacing the incoming header.
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        try:
            chain = [
                ipaddress.ip_address(value.strip())
                for value in forwarded.split(",")
                if value.strip()
            ]
        except ValueError:
            return str(peer_ip)
        if not chain:
            return str(peer_ip)

        current = peer_ip
        for candidate in reversed(chain):
            if not any(current in network for network in trusted_networks):
                return str(current)
            current = candidate
        return str(current)

    real = request.headers.get("x-real-ip", "").strip()
    if real:
        try:
            return str(ipaddress.ip_address(real))
        except ValueError:
            return str(peer_ip)
    return str(peer_ip)


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
    email_key = hashlib.sha256(email.encode("utf-8")).hexdigest()[:20]
    acct_key = f"login:{ip}:{email_key}"
    ip_key = f"login-ip:{ip}"
    reserved, reservation_token = rate_limit.reserve_many([
        (acct_key, _LOGIN_MAX_FAILURES, _LOGIN_WINDOW_S),
        (ip_key, _LOGIN_IP_MAX_FAILURES, _LOGIN_WINDOW_S),
    ])
    if not reserved:
        log_event("login_rate_limited", email=email, ip=ip)
        raise HTTPException(429, "Too many failed attempts, try again later")
    user = authenticate_user(email, req.password)
    if not user:
        log_event("login_failed", email=email, ip=ip)
        raise HTTPException(401, "Invalid email or password")
    # The reservation protects the bcrypt interval. Successful attempts do not
    # count toward the failed-attempt windows, so remove exactly this request's
    # event without clearing failures from concurrent requests.
    rate_limit.release_record(acct_key, reservation_token)
    rate_limit.release_record(ip_key, reservation_token)
    log_event("login_success", user_id=user["id"], email=user["email"], ip=ip)
    token_version = user.pop("_token_version", None)
    token = (
        create_token(user["id"])
        if token_version is None
        else create_token(user["id"], int(token_version))
    )
    return {"token": token, "user": user}


@router.get("/auth/me")
def get_me(user_id: str = Depends(get_current_user)):
    user = get_user_by_id(user_id)
    if not user:
        raise HTTPException(404, "User not found")
    return _public_user(user)


@router.put("/auth/profile")
def update_profile(req: UpdateProfileRequest, request: Request, user_id: str = Depends(get_current_user)):
    user = update_user_profile(user_id, name=req.name, email=req.email)
    log_event("profile_updated", user_id=user_id, ip=client_ip(request),
              detail={"fields": [k for k, v in (("name", req.name), ("email", req.email)) if v is not None]})
    return {"ok": True, "user": _public_user(user)}


@router.put("/auth/password")
def change_password(req: ChangePasswordRequest, request: Request, user_id: str = Depends(get_current_user)):
    validate_new_password(req.new_password)
    token_version = change_user_password(
        user_id, req.current_password, req.new_password,
    )
    if token_version is None:
        log_event("password_change_failed", user_id=user_id, ip=client_ip(request))
        raise HTTPException(400, "Current password is incorrect")
    log_event("password_changed", user_id=user_id, ip=client_ip(request))
    return {"ok": True, "token": create_token(user_id, token_version)}


@router.get("/")
def root():
    return {"service": "SparkOffer", "version": "0.3.0"}
