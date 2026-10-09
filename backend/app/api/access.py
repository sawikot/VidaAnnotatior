"""Who is asking, and may they?

Every data route (all routers but the sign-in ones) runs ``authorize``:

1. there must be a signed-in, active user (else 401);
2. the project the route touches -- found from ``project_id``, ``slide_id``, ``patch_id``,
   ``annotation_id``, ``suggestion_id``, ``run_id`` or ``config_id`` in the path -- must be one the user may see: admins see
   every project, everyone else the projects they are a member of (else 403);
3. the route must be allowed for the user's role: reading, and the annotation work listed in
   ``ANNOTATOR_WRITES``, for everyone; any other change for managers and admins only (else 403).

Keeping the whole policy here means a new route is protected the moment it is added.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.database.session import get_db
from app.models.annotation import GeometryAnnotation
from app.models.config_version import ProjectConfigVersion
from app.models.patch import Patch
from app.models.slide import Slide
from app.models.training import Suggestion, TrainedModel, TrainingRun
from app.models.user import ProjectMember, User
from app.services.auth import SESSION_COOKIE, user_for_session

READ_METHODS = {"GET", "HEAD", "OPTIONS"}

# Changes every member may make: annotating, labelling and reviewing, and choosing which of a slide's
# patch sizes to work in.
ANNOTATOR_WRITES = {
    ("PUT", "/patches/{patch_id}"),
    ("POST", "/slides/{slide_id}/annotations"),
    ("POST", "/patches/{patch_id}/annotations"),
    ("PUT", "/annotations/{annotation_id}"),
    ("DELETE", "/annotations/{annotation_id}"),
    ("PUT", "/slides/{slide_id}/active-grid"),
    ("POST", "/slides/{slide_id}/patches/label"),
    # ...and asking a model for suggestions, and deciding on them.
    ("POST", "/patches/{patch_id}/suggest"),
    ("POST", "/patches/{patch_id}/suggestions/accept"),
    ("DELETE", "/patches/{patch_id}/suggestions"),
    ("POST", "/suggestions/{suggestion_id}/accept"),
    ("POST", "/suggestions/{suggestion_id}/reject"),
    ("POST", "/slides/{slide_id}/suggest"),
    ("DELETE", "/slides/{slide_id}/suggest"),
    ("POST", "/slides/{slide_id}/suggestions/accept"),
    ("DELETE", "/slides/{slide_id}/suggestions"),
}

# Routes that touch no single project: reading the list (filtered to the user's projects) and the
# supported formats; creating a project (managers).
NO_PROJECT = {
    ("GET", "/projects"),
    ("GET", "/wsi-formats"),
    ("GET", "/training/recipes"),
    ("GET", "/training/status"),
    ("GET", "/training/importers"),
}
MANAGER_NO_PROJECT = {("POST", "/projects")}


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = user_for_session(db, request.cookies.get(SESSION_COOKIE))
    if user is None:
        raise HTTPException(status_code=401, detail="Please sign in.")
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Only an administrator can do this.")
    return user


def require_manager(user: User = Depends(current_user)) -> User:
    if not user.can_manage:
        raise HTTPException(status_code=403, detail="Only a project manager or an administrator can do this.")
    return user


def project_of(db: Session, params: dict) -> int | None:
    """The project a route's path points into; 404 when the thing it names does not exist."""

    def missing(what: str, value) -> HTTPException:
        return HTTPException(status_code=404, detail=f"{what} {value} not found")

    if "project_id" in params:
        return int(params["project_id"])
    if "slide_id" in params:
        row = db.query(Slide.project_id).filter(Slide.id == int(params["slide_id"])).first()
        if row is None:
            raise missing("Slide", params["slide_id"])
        return row[0]
    if "patch_id" in params:
        row = db.query(Slide.project_id).join(Patch, Patch.slide_id == Slide.id).filter(Patch.id == int(params["patch_id"])).first()
        if row is None:
            raise missing("Patch", params["patch_id"])
        return row[0]
    if "annotation_id" in params:
        row = (
            db.query(Slide.project_id)
            .join(GeometryAnnotation, GeometryAnnotation.slide_id == Slide.id)
            .filter(GeometryAnnotation.id == int(params["annotation_id"]))
            .first()
        )
        if row is None:
            raise missing("Annotation", params["annotation_id"])
        return row[0]
    if "suggestion_id" in params:
        row = db.query(Slide.project_id).join(Suggestion, Suggestion.slide_id == Slide.id).filter(Suggestion.id == int(params["suggestion_id"])).first()
        if row is None:
            raise missing("Suggestion", params["suggestion_id"])
        return row[0]
    if "model_id" in params:
        row = db.query(TrainedModel.project_id).filter(TrainedModel.id == int(params["model_id"])).first()
        if row is None:
            raise missing("Model", params["model_id"])
        return row[0]
    if "run_id" in params:
        row = db.query(TrainingRun.project_id).filter(TrainingRun.id == int(params["run_id"])).first()
        if row is None:
            raise missing("Training run", params["run_id"])
        return row[0]
    if "config_id" in params:
        row = db.query(ProjectConfigVersion.project_id).filter(ProjectConfigVersion.id == int(params["config_id"])).first()
        if row is None:
            raise missing("Configuration", params["config_id"])
        return row[0]
    return None


def is_member(db: Session, user: User, project_id: int) -> bool:
    if user.is_admin:
        return True
    return db.query(ProjectMember.id).filter(ProjectMember.project_id == project_id, ProjectMember.user_id == user.id).first() is not None


def authorize(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)) -> User:
    route = request.scope.get("route")
    prefix = get_settings().api_prefix
    template = getattr(route, "path", request.url.path)
    template = template[len(prefix):] if template.startswith(prefix) else template
    method = request.method
    key = (method, template)

    if key in NO_PROJECT:
        return user
    if key in MANAGER_NO_PROJECT:
        if not user.can_manage:
            raise HTTPException(status_code=403, detail="Only a project manager or an administrator can create projects.")
        return user

    try:
        project_id = project_of(db, request.path_params)
    except ValueError as exc:  # a non-numeric id: let the route's own validation answer
        raise HTTPException(status_code=422, detail="Invalid id in the address") from exc
    if project_id is None:
        # A route with no project in its path that is not listed above: refuse rather than guess.
        raise HTTPException(status_code=403, detail="Not allowed.")
    if not is_member(db, user, project_id):
        raise HTTPException(status_code=403, detail="You are not a member of this project.")
    if method in READ_METHODS or key in ANNOTATOR_WRITES:
        return user
    if not user.can_manage:
        raise HTTPException(status_code=403, detail="Only a project manager or an administrator can change this.")
    return user
