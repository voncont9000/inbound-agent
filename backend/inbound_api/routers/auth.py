from fastapi import APIRouter, HTTPException

from inbound_api.schemas import (
    AuthResponse,
    LoginRequest,
    LogoutResponse,
    MeResponse,
    SignupRequest,
)

router = APIRouter(tags=["auth"])


@router.post("/auth/signup", response_model=AuthResponse, status_code=201)
async def signup(payload: SignupRequest) -> AuthResponse:
    raise HTTPException(status_code=501, detail="Not implemented")


@router.post("/auth/login", response_model=AuthResponse)
async def login(payload: LoginRequest) -> AuthResponse:
    raise HTTPException(status_code=501, detail="Not implemented")


@router.post("/auth/logout", response_model=LogoutResponse)
async def logout() -> LogoutResponse:
    raise HTTPException(status_code=501, detail="Not implemented")


@router.get("/me", response_model=MeResponse)
async def me() -> MeResponse:
    raise HTTPException(status_code=501, detail="Not implemented")
