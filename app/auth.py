import hashlib
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from fastapi_users import BaseUserManager, UUIDIDMixin, exceptions, schemas
from fastapi_users.authentication import CookieTransport
from fastapi_users.authentication.strategy.db import DatabaseStrategy
from fastapi_users.db import SQLAlchemyUserDatabase
from fastapi_users_db_sqlalchemy.access_token import SQLAlchemyAccessTokenDatabase
from sqlalchemy import case
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_session
from .errors import APIError
from .models import AccessToken, AuthAttempt, User, utcnow
from .schemas import Login, Register

COOKIE = "e2e_session"


class UserCreate(schemas.BaseUserCreate):
    name: str


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    async def validate_password(self, password, user):
        if len(password) < 12 or len(password) > 128:
            raise exceptions.InvalidPasswordException(reason="Password must contain 12 to 128 characters")


def components(session: AsyncSession, request):
    manager = UserManager(SQLAlchemyUserDatabase(session, User))
    strategy = DatabaseStrategy(
        SQLAlchemyAccessTokenDatabase(session, AccessToken),
        lifetime_seconds=request.app.state.settings.session_lifetime,
    )
    return manager, strategy


def public_user(user):
    return {"id": str(user.id), "name": user.name, "email": user.email}


async def resolve_user(request, session):
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    manager, strategy = components(session, request)
    user = await strategy.read_token(token, manager)
    return user if user and user.is_active else None


async def current_user(request: Request, session: AsyncSession = Depends(get_session)):
    user = await resolve_user(request, session)
    if user is None:
        raise APIError(401, "UNAUTHENTICATED", "Sign in to continue")
    return user


async def session_payload(request, session, user, token):
    access = await session.get(AccessToken, token)
    return {
        "user": public_user(user),
        "session": {
            "id": hashlib.sha256(token.encode()).hexdigest()[:32],
            "userId": str(user.id),
            "expiresAt": (
                access.created_at + timedelta(seconds=request.app.state.settings.session_lifetime)
            ).isoformat(),
        },
    }


async def throttle(request, session, email):
    bucket = hashlib.sha256(
        f"{request.client.host if request.client else 'local'}:{email.lower()}".encode()
    ).hexdigest()
    at = utcnow()
    statement = insert(AuthAttempt).values(key=bucket, count=1, expires_at=at + timedelta(minutes=5))
    statement = statement.on_conflict_do_update(
        index_elements=[AuthAttempt.key],
        set_={
            "count": case((AuthAttempt.expires_at < at, 1), else_=AuthAttempt.count + 1),
            "expires_at": case((AuthAttempt.expires_at < at, at + timedelta(minutes=5)), else_=AuthAttempt.expires_at),
        },
    ).returning(AuthAttempt.count)
    count = await session.scalar(statement)
    await session.commit()
    if count > request.app.state.settings.auth_rate_limit:
        raise APIError(429, "RATE_LIMITED", "Too many authentication attempts; retry in five minutes")


router = APIRouter(prefix="/api/auth", tags=["Authentication"])


async def login_response(request, session, user, status):
    _, strategy = components(session, request)
    token = await strategy.write_token(user)
    response = JSONResponse(await session_payload(request, session, user, token), status_code=status)
    cookie_response = await CookieTransport(
        cookie_name=COOKIE,
        cookie_max_age=request.app.state.settings.session_lifetime,
        cookie_secure=request.app.state.settings.cookie_secure,
        cookie_samesite="lax",
    ).get_login_response(token)
    response.headers.append("set-cookie", cookie_response.headers["set-cookie"])
    return response


@router.post("/sign-up/email", status_code=201)
async def register(body: Register, request: Request, session: AsyncSession = Depends(get_session)):
    await throttle(request, session, body.email)
    manager, _ = components(session, request)
    try:
        user = await manager.create(UserCreate(**body.model_dump()), safe=True, request=request)
    except exceptions.UserAlreadyExists as exc:
        raise APIError(409, "USER_ALREADY_EXISTS", "An account with this email already exists") from exc
    return await login_response(request, session, user, 201)


@router.post("/sign-in/email")
async def login(body: Login, request: Request, session: AsyncSession = Depends(get_session)):
    await throttle(request, session, body.email)
    manager, _ = components(session, request)
    user = await manager.authenticate(OAuth2PasswordRequestForm(username=body.email, password=body.password, scope=""))
    if not user or not user.is_active:
        raise APIError(401, "INVALID_CREDENTIALS", "Invalid email or password")
    return await login_response(request, session, user, 200)


@router.get("/get-session")
async def get_current_session(request: Request, session: AsyncSession = Depends(get_session)):
    user = await resolve_user(request, session)
    if user is None:
        return None
    return await session_payload(request, session, user, request.cookies[COOKIE])


@router.post("/sign-out")
async def logout(request: Request, session: AsyncSession = Depends(get_session)):
    user = await resolve_user(request, session)
    if user:
        _, strategy = components(session, request)
        await strategy.destroy_token(request.cookies[COOKIE], user)
    response = JSONResponse({"success": True})
    response.delete_cookie(COOKIE, secure=request.app.state.settings.cookie_secure, httponly=True, samesite="lax")
    return response
