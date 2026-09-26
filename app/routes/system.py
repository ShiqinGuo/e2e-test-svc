from fastapi import APIRouter, Depends

from .. import responses as out
from ..auth import current_user, public_user
from ..context import Context, get_context
from ..models import User

P = "/api/v1/projects/{projectId}"
router = APIRouter()


@router.get("/api/health", tags=["System"], response_model=out.Health)
async def health():
    return {"status": "ok"}


@router.get("/api/v1/me", tags=["Authentication"], response_model=out.CurrentUser)
async def me(user: User = Depends(current_user)):
    return {"user": public_user(user)}


@router.get("/api/v1/capabilities", tags=["System"], response_model=out.Capabilities)
async def capabilities(
    user: User = Depends(current_user),
    context: Context = Depends(get_context),
):
    return {"playwrightVersion": "1.63.0", **await context.runtime.available(), "databaseChecks": "api-only"}
