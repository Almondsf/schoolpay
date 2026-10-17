import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, StringConstraints

from .models import SchoolStatus

Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)]


class SchoolOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    status: SchoolStatus
    address: str
    phone: str
    email: str
    created_at: datetime


class SchoolUpdateIn(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=200)] | None = None
    address: Text | None = None
    phone: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^\+?[0-9 ()-]{7,20}$")] | None = None
    email: EmailStr | None = None


class SchoolCreateIn(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=200)]
    slug: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=60)]
    proprietor_email: EmailStr
    proprietor_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=150)]


class SchoolStatusIn(BaseModel):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=300)]
