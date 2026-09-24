"""Signing in and out, the first administrator, password links; and managing users and project members."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.access import authorize, current_user, require_admin, require_manager
from app.core.config import get_settings
from app.database.session import get_db
from app.models.project import Project
from app.models.user import ROLES, ProjectMember, User
from app.services import auth

router = APIRouter(tags=["auth"])


class UserOut(BaseModel):
    id: int
    email: str
    name: str
    role: str
    is_active: bool
    has_password: bool = False
    project_count: int = 0

    @classmethod
    def of(cls, user: User) -> "UserOut":
        return cls(
            id=user.id, email=user.email, name=user.name, role=user.role, is_active=user.is_active,
            has_password=bool(user.password_hash), project_count=len(user.memberships),
        )


class StatusOut(BaseModel):
    needs_setup: bool
    user: UserOut | None


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class SetupIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=254)
    password: str


class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: str


class PasswordLinkIn(BaseModel):
    token: str
    password: str


class UserCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=254)
    role: str = "annotator"
    password: str | None = None  # none: the answer carries a link for the person to set one


class UserUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    role: str | None = None
    is_active: bool | None = None


class UserCreatedOut(BaseModel):
    user: UserOut
    password_link_token: str | None = None  # the frontend turns it into /set-password?token=...


class MemberOut(BaseModel):
    id: int
    name: str
    email: str
    role: str
    is_active: bool


class MemberIn(BaseModel):
    user_id: int


# ------------------------------------------------------------------ helpers


def _set_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        auth.SESSION_COOKIE, token, max_age=settings.session_days * 86400, httponly=True,
        samesite="lax", secure=settings.cookie_secure, path="/",
    )


def _client_key(request: Request, email: str) -> str:
    host = request.client.host if request.client else "?"
    return f"{auth.normalize_email(email)}|{host}"


def _check_email(email: str) -> str:
    email = auth.normalize_email(email)
    if "@" not in email or email.startswith("@") or email.endswith("@") or " " in email:
        raise HTTPException(status_code=422, detail="Enter a valid email address.")
    return email


def _check_password(password: str) -> None:
    problem = auth.password_problem(password)
    if problem:
        raise HTTPException(status_code=422, detail=problem)


def _check_role(role: str) -> None:
    if role not in ROLES:
        raise HTTPException(status_code=422, detail=f"role must be one of: {', '.join(ROLES)}")


def _active_admins(db: Session) -> int:
    return db.query(User).filter(User.role == "admin", User.is_active.is_(True)).count()


# ------------------------------------------------------------------ signing in


@router.get("/auth/status", response_model=StatusOut)
def status(request: Request, db: Session = Depends(get_db)) -> StatusOut:
    """Whether the app still needs its first administrator, and who is signed in (if anyone)."""
    needs_setup = db.query(User.id).first() is None
    user = auth.user_for_session(db, request.cookies.get(auth.SESSION_COOKIE))
    return StatusOut(needs_setup=needs_setup, user=UserOut.of(user) if user else None)


@router.post("/auth/setup", response_model=UserOut, status_code=201)
def setup(payload: SetupIn, response: Response, db: Session = Depends(get_db)) -> UserOut:
    """Create the first administrator. Only possible while there are no users at all."""
    if db.query(User.id).first() is not None:
        raise HTTPException(status_code=409, detail="The app is already set up; sign in instead.")
    _check_password(payload.password)
    user = User(email=_check_email(payload.email), name=payload.name.strip(), role="admin", password_hash=auth.hash_password(payload.password))
    db.add(user)
    db.flush()
    _set_cookie(response, auth.create_session(db, user, get_settings().session_days))
    db.commit()
    return UserOut.of(user)


@router.post("/auth/login", response_model=UserOut)
def login(payload: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)) -> UserOut:
    key = _client_key(request, payload.email)
    if auth.login_throttle.blocked(key):
        raise HTTPException(status_code=429, detail="Too many failed attempts. Wait 15 minutes and try again.")
    user = auth.check_credentials(db, payload.email, payload.password)
    if user is None:
        auth.login_throttle.failed(key)
        raise HTTPException(status_code=401, detail="Wrong email or password.")
    auth.login_throttle.clear(key)
    _set_cookie(response, auth.create_session(db, user, get_settings().session_days))
    db.commit()
    return UserOut.of(user)


@router.post("/auth/logout", status_code=204, response_model=None)
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> None:
    auth.end_session(db, request.cookies.get(auth.SESSION_COOKIE))
    db.commit()
    response.delete_cookie(auth.SESSION_COOKIE, path="/")


@router.get("/auth/me", response_model=UserOut)
def me(user: User = Depends(current_user)) -> UserOut:
    return UserOut.of(user)


@router.post("/auth/password", status_code=204, response_model=None)
def change_password(payload: PasswordChangeIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)) -> None:
    if not auth.verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=422, detail="The current password is wrong.")
    _check_password(payload.new_password)
    user.password_hash = auth.hash_password(payload.new_password)
    auth.end_all_sessions(db, user, keep=request.cookies.get(auth.SESSION_COOKIE))  # sign out other devices
    db.commit()


@router.get("/auth/password-link/{token}", response_model=UserOut)
def password_link(token: str, db: Session = Depends(get_db)) -> UserOut:
    """Who a password link is for (the set-password page greets them); 404 when used or expired."""
    user = auth.user_for_password_token(db, token)
    if user is None:
        raise HTTPException(status_code=404, detail="This link has expired or was already used. Ask an administrator for a new one.")
    return UserOut.of(user)


@router.post("/auth/password-link", response_model=UserOut)
def use_password_link(payload: PasswordLinkIn, response: Response, db: Session = Depends(get_db)) -> UserOut:
    user = auth.user_for_password_token(db, payload.token)
    if user is None:
        raise HTTPException(status_code=404, detail="This link has expired or was already used. Ask an administrator for a new one.")
    _check_password(payload.password)
    user.password_hash = auth.hash_password(payload.password)
    auth.use_password_token(db, payload.token)
    auth.end_all_sessions(db, user)
    _set_cookie(response, auth.create_session(db, user, get_settings().session_days))
    db.commit()
    return UserOut.of(user)


# ------------------------------------------------------------------ users (administrators)


@router.get("/users", response_model=list[UserOut])
def list_users(_: User = Depends(require_manager), db: Session = Depends(get_db)) -> list[UserOut]:
    """Everyone (managers need the list to add members to their projects)."""
    return [UserOut.of(u) for u in db.query(User).order_by(User.name.asc(), User.id.asc())]


@router.post("/users", response_model=UserCreatedOut, status_code=201)
def create_user(payload: UserCreateIn, _: User = Depends(require_admin), db: Session = Depends(get_db)) -> UserCreatedOut:
    email = _check_email(payload.email)
    _check_role(payload.role)
    if db.query(User.id).filter(User.email == email).first() is not None:
        raise HTTPException(status_code=409, detail=f"There is already an account for {email}.")
    user = User(email=email, name=payload.name.strip(), role=payload.role)
    if payload.password:
        _check_password(payload.password)
        user.password_hash = auth.hash_password(payload.password)
    db.add(user)
    db.flush()
    token = None if payload.password else auth.create_password_token(db, user)
    db.commit()
    return UserCreatedOut(user=UserOut.of(user), password_link_token=token)


@router.put("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, payload: UserUpdateIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> UserOut:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")
    losing_admin = user.role == "admin" and user.is_active and (
        (payload.role is not None and payload.role != "admin") or payload.is_active is False
    )
    if losing_admin and _active_admins(db) <= 1:
        raise HTTPException(status_code=409, detail="This is the only active administrator; make someone else an administrator first.")
    if payload.name is not None:
        user.name = payload.name.strip()
    if payload.role is not None:
        _check_role(payload.role)
        user.role = payload.role
    if payload.is_active is not None:
        user.is_active = payload.is_active
        if not payload.is_active:
            auth.end_all_sessions(db, user)  # disabled: signed out everywhere at once
    db.commit()
    return UserOut.of(user)


@router.post("/users/{user_id}/password-link", response_model=UserCreatedOut)
def new_password_link(user_id: int, _: User = Depends(require_admin), db: Session = Depends(get_db)) -> UserCreatedOut:
    """A fresh one-time link to (re)set the person's password; older links stop working."""
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")
    token = auth.create_password_token(db, user)
    db.commit()
    return UserCreatedOut(user=UserOut.of(user), password_link_token=token)


# ------------------------------------------------------------------ project members


def _members(db: Session, project_id: int) -> list[MemberOut]:
    rows = (
        db.query(User)
        .join(ProjectMember, ProjectMember.user_id == User.id)
        .filter(ProjectMember.project_id == project_id)
        .order_by(User.name.asc())
        .all()
    )
    return [MemberOut(id=u.id, name=u.name, email=u.email, role=u.role, is_active=u.is_active) for u in rows]


@router.get("/projects/{project_id}/members", response_model=list[MemberOut], dependencies=[Depends(authorize)])
def list_members(project_id: int, db: Session = Depends(get_db)) -> list[MemberOut]:
    return _members(db, project_id)


@router.post("/projects/{project_id}/members", response_model=list[MemberOut], dependencies=[Depends(authorize)])
def add_member(project_id: int, payload: MemberIn, db: Session = Depends(get_db)) -> list[MemberOut]:
    if db.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
    user = db.get(User, payload.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail=f"User {payload.user_id} not found")
    if db.query(ProjectMember.id).filter(ProjectMember.project_id == project_id, ProjectMember.user_id == user.id).first() is None:
        db.add(ProjectMember(project_id=project_id, user_id=user.id))
        db.commit()
    return _members(db, project_id)


@router.delete("/projects/{project_id}/members/{user_id}", response_model=list[MemberOut], dependencies=[Depends(authorize)])
def remove_member(project_id: int, user_id: int, db: Session = Depends(get_db)) -> list[MemberOut]:
    db.query(ProjectMember).filter(ProjectMember.project_id == project_id, ProjectMember.user_id == user_id).delete(synchronize_session=False)
    db.commit()
    return _members(db, project_id)
