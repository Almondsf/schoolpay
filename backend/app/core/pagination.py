"""Limit/offset pagination with a total count, in one consistent response shape."""

from typing import Annotated, Any

from fastapi import Query
from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session


class PageParams(BaseModel):
    limit: int
    offset: int


def page_params(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PageParams:
    return PageParams(limit=limit, offset=offset)


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


def paginate(session: Session, stmt: Select[Any], params: PageParams) -> tuple[list[Any], int]:
    """Run `stmt` for one page, plus a count of all matching rows. Both are tenant-scoped."""
    total = session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = list(session.scalars(stmt.limit(params.limit).offset(params.offset)))
    return rows, total
