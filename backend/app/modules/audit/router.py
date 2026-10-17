import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import ERROR_RESPONSES
from app.core.pagination import Page, PageParams, page_params, paginate
from app.modules.accounts.deps import Principal, StaffContext, require, require_platform_admin
from app.modules.accounts.permissions import Capability

from . import service

audit_router = APIRouter(prefix="/audit", tags=["audit"], responses=ERROR_RESPONSES)
platform_audit_router = APIRouter(prefix="/platform/audit", tags=["platform"], responses=ERROR_RESPONSES)


class AuditEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    actor_user_id: uuid.UUID | None
    actor_label: str
    action: str
    target_type: str
    target_id: str
    changes: dict[str, Any]
    ip_address: str


@audit_router.get("", response_model=Page[AuditEntryOut], summary="The school's audit trail, newest first")
def list_audit(
    ctx: StaffContext = Depends(require(Capability.AUDIT_VIEW)),
    db: Session = Depends(get_db),
    params: PageParams = Depends(page_params),
    action: str | None = Query(default=None, max_length=64),
    actor_user_id: uuid.UUID | None = None,
    target_type: str | None = Query(default=None, max_length=64),
    target_id: str | None = Query(default=None, max_length=64),
) -> Page[AuditEntryOut]:
    stmt = service.school_entries(
        action=action, actor_user_id=actor_user_id, target_type=target_type, target_id=target_id
    )
    rows, total = paginate(db, stmt, params)
    return Page(
        items=[AuditEntryOut.model_validate(r) for r in rows], total=total, limit=params.limit, offset=params.offset
    )


@platform_audit_router.get("", response_model=Page[AuditEntryOut], summary="Platform-level audit trail")
def list_platform_audit(
    admin: Principal = Depends(require_platform_admin),
    db: Session = Depends(get_db),
    params: PageParams = Depends(page_params),
    action: str | None = Query(default=None, max_length=64),
) -> Page[AuditEntryOut]:
    rows, total = paginate(db, service.platform_entries(action=action), params)
    return Page(
        items=[AuditEntryOut.model_validate(r) for r in rows], total=total, limit=params.limit, offset=params.offset
    )
