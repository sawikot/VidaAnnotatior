from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.models.project import Project


def slugify(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", name.strip()).strip("_").upper()
    return slug or "PROJECT"


def unique_project_slug(db: Session, base_slug: str) -> str:
    slug = base_slug
    n = 1
    while db.query(Project.id).filter(Project.slug == slug).first() is not None:
        n += 1
        slug = f"{base_slug}_{n}"
    return slug
