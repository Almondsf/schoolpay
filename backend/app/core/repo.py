"""Small query helpers shared by services."""

import uuid

from sqlalchemy.orm import Session

from app.core.errors import NotFound


def get_or_404[T](
    session: Session,
    model: type[T],
    object_id: uuid.UUID,
    *,
    label: str | None = None,
    field: str | None = None,
    lock: bool = False,
) -> T:
    """
    Load by primary key within the session's tenant scope. Another school's ID is
    simply not found, so IDs in URLs and request bodies can't reach across schools.
    """
    obj = session.get(model, object_id, with_for_update=lock)
    if obj is None:
        raise NotFound(f"{label or model.__name__} not found.", field=field)
    return obj
