from functools import lru_cache

from fastapi import HTTPException, Request, status
from supabase import create_client, Client

from app.core.config import get_settings


@lru_cache()
def _get_supabase() -> Client:
    settings = get_settings()
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_ANON_KEY)


async def verify_token(request: Request):
    auth_header = request.headers.get("authorization", "")

    if not auth_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization header",
        )

    parts = auth_header.split(" ")
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header format",
        )

    token = parts[1]

    try:
        supabase = _get_supabase()
        response = supabase.auth.get_user(token)
        if not response.user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
            )
        return response.user
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token verification failed",
        )
