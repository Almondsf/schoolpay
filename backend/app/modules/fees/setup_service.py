"""
Fee setup: fee items, per-term schedules, optional-item choices and discounts.

Changing a schedule or discount affects invoices generated afterwards only;
issued invoices are never rewritten (they're ledger records).
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import Select, and_, delete, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import Conflict, DomainError
from app.core.repo import get_or_404
from app.modules.accounts.deps import StaffContext
from app.modules.audit.models import AuditAction
from app.modules.audit.service import changed_fields, record
from app.modules.schools.models import ClassLevel, Term
from app.modules.students.models import Student

from .models import (
    Discount,
    DiscountKind,
    DiscountRule,
    FeeCategory,
    FeeItem,
    FeeSchedule,
    StudentDiscount,
    StudentFeeChoice,
)

MAX_AMOUNT_KOBO = 100_000_000_00  # ₦100m: anything bigger is a typo


def _audit(session: Session, ctx: StaffContext, target: object, changes: dict[str, object]) -> None:
    record(
        session,
        AuditAction.FEES_CHANGED,
        school_id=ctx.school_id,
        actor=ctx.actor,
        meta=ctx.meta,
        target=target,
        changes=changes,
    )


def _flush_unique(session: Session, message: str, field: str) -> None:
    try:
        session.flush()
    except IntegrityError as e:
        session.rollback()
        raise Conflict(message, field=field) from e


# --- Fee items -------------------------------------------------------------------------------------


def list_fee_items(session: Session) -> list[FeeItem]:
    return list(session.scalars(select(FeeItem).order_by(FeeItem.is_active.desc(), FeeItem.name)))


def create_fee_item(session: Session, ctx: StaffContext, *, name: str, category: FeeCategory) -> FeeItem:
    item = FeeItem(id=uuid.uuid4(), name=name.strip(), category=category)
    session.add(item)
    _flush_unique(session, f"There's already a fee called {name.strip()}.", "name")
    _audit(session, ctx, item, {"created": item.name, "category": category})
    session.commit()
    return item


def update_fee_item(
    session: Session,
    ctx: StaffContext,
    item_id: uuid.UUID,
    *,
    name: str | None,
    category: FeeCategory | None,
    is_active: bool | None,
) -> FeeItem:
    item = get_or_404(session, FeeItem, item_id, label="Fee", lock=True)
    before = {"name": item.name, "category": item.category, "is_active": item.is_active}
    if name is not None:
        item.name = name.strip()
    if category is not None:
        item.category = category
    if is_active is not None:
        item.is_active = is_active
    _flush_unique(session, "Another fee already has that name.", "name")
    diff = changed_fields(before, {"name": item.name, "category": item.category, "is_active": item.is_active})
    if diff:
        _audit(session, ctx, item, diff)
    session.commit()
    return item


# --- Schedules -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ScheduleEntry:
    fee_item_id: uuid.UUID
    class_level_id: uuid.UUID
    amount_kobo: int | None  # None removes the entry
    is_optional: bool = False


def schedule_for_term(session: Session, term_id: uuid.UUID) -> list[FeeSchedule]:
    get_or_404(session, Term, term_id, label="Term")
    return list(session.scalars(select(FeeSchedule).where(FeeSchedule.term_id == term_id)))


def _check_ids(session: Session, model: type[FeeItem] | type[ClassLevel], ids: set[uuid.UUID], field: str) -> None:
    """Every id must belong to this school (one query for all of them)."""
    if not ids:
        return
    found = set(session.scalars(select(model.id).where(model.id.in_(ids))))
    if found != ids:
        raise DomainError(f"Unknown {field.replace('_id', '').replace('_', ' ')}.", field=field)


def set_schedule(
    session: Session, ctx: StaffContext, term_id: uuid.UUID, entries: list[ScheduleEntry]
) -> list[FeeSchedule]:
    """Set (or remove) the amounts for some item/class pairs in a term. Pairs not listed are left alone."""
    term = get_or_404(session, Term, term_id, label="Term")
    _check_ids(session, FeeItem, {e.fee_item_id for e in entries}, "fee_item_id")
    _check_ids(session, ClassLevel, {e.class_level_id for e in entries}, "class_level_id")
    if len({(e.fee_item_id, e.class_level_id) for e in entries}) != len(entries):
        raise DomainError("Each fee and class appears more than once.", field="entries")
    for e in entries:
        if e.amount_kobo is not None and not 0 < e.amount_kobo <= MAX_AMOUNT_KOBO:
            raise DomainError("Amounts must be more than ₦0 and at most ₦100,000,000.", field="entries")
    removals = [(e.fee_item_id, e.class_level_id) for e in entries if e.amount_kobo is None]
    upserts = [e for e in entries if e.amount_kobo is not None]
    if removals:
        session.execute(
            delete(FeeSchedule).where(
                FeeSchedule.term_id == term.id,
                tuple_(FeeSchedule.fee_item_id, FeeSchedule.class_level_id).in_(removals),
            )
        )
    if upserts:
        stmt = insert(FeeSchedule).values(
            [
                {
                    "id": uuid.uuid4(),
                    "school_id": ctx.school_id,
                    "term_id": term.id,
                    "fee_item_id": e.fee_item_id,
                    "class_level_id": e.class_level_id,
                    "amount_kobo": e.amount_kobo,
                    "is_optional": e.is_optional,
                }
                for e in upserts
            ]
        )
        session.execute(
            stmt.on_conflict_do_update(
                index_elements=["term_id", "class_level_id", "fee_item_id"],
                set_={"amount_kobo": stmt.excluded.amount_kobo, "is_optional": stmt.excluded.is_optional},
            )
        )
    _audit(session, ctx, term, {"schedule_set": len(upserts), "schedule_removed": len(removals)})
    session.commit()
    session.expire_all()
    return schedule_for_term(session, term.id)


def copy_schedule(session: Session, ctx: StaffContext, *, from_term_id: uuid.UUID, to_term_id: uuid.UUID) -> int:
    """Copy one term's fee amounts into another. Amounts already set in the target term are kept."""
    source = get_or_404(session, Term, from_term_id, label="Term", field="from_term_id")
    target = get_or_404(session, Term, to_term_id, label="Term")
    if source.id == target.id:
        raise DomainError("Choose a different term to copy from.", field="from_term_id")
    rows = session.execute(
        select(
            FeeSchedule.fee_item_id, FeeSchedule.class_level_id, FeeSchedule.amount_kobo, FeeSchedule.is_optional
        ).where(FeeSchedule.term_id == source.id)
    ).all()
    if not rows:
        return 0
    stmt = (
        insert(FeeSchedule)
        .values(
            [
                {
                    "id": uuid.uuid4(),
                    "school_id": ctx.school_id,
                    "term_id": target.id,
                    "fee_item_id": item,
                    "class_level_id": level,
                    "amount_kobo": amount,
                    "is_optional": optional,
                }
                for item, level, amount, optional in rows
            ]
        )
        .on_conflict_do_nothing(index_elements=["term_id", "class_level_id", "fee_item_id"])
        .returning(FeeSchedule.id)
    )
    copied = len(session.execute(stmt).all())
    _audit(session, ctx, target, {"copied_from": source.id, "entries": copied})
    session.commit()
    return int(copied)


# --- Optional items ------------------------------------------------------------------------------


def choices_for_term(
    session: Session, term_id: uuid.UUID, fee_item_id: uuid.UUID | None = None
) -> Select[uuid.UUID, uuid.UUID]:
    stmt = select(StudentFeeChoice.student_id, StudentFeeChoice.fee_item_id).where(StudentFeeChoice.term_id == term_id)
    if fee_item_id:
        stmt = stmt.where(StudentFeeChoice.fee_item_id == fee_item_id)
    return stmt


def set_choices(
    session: Session,
    ctx: StaffContext,
    *,
    term_id: uuid.UUID,
    fee_item_id: uuid.UUID,
    student_ids: list[uuid.UUID],
    selected: bool,
) -> int:
    """Opt students in to (or out of) an optional item for a term. Affects invoices generated afterwards."""
    term = get_or_404(session, Term, term_id, label="Term", field="term_id")
    item = get_or_404(session, FeeItem, fee_item_id, label="Fee", field="fee_item_id")
    ids = set(student_ids)
    found = set(session.scalars(select(Student.id).where(Student.id.in_(ids)))) if ids else set()
    if found != ids:
        raise DomainError("Unknown student.", field="student_ids")
    if selected:
        stmt = (
            insert(StudentFeeChoice)
            .values(
                [
                    {"school_id": ctx.school_id, "student_id": sid, "term_id": term.id, "fee_item_id": item.id}
                    for sid in ids
                ]
            )
            .on_conflict_do_nothing()
            .returning(StudentFeeChoice.student_id)
        )
        changed = len(session.execute(stmt).all()) if ids else 0
    else:
        removed = session.execute(
            delete(StudentFeeChoice)
            .where(
                StudentFeeChoice.term_id == term.id,
                StudentFeeChoice.fee_item_id == item.id,
                StudentFeeChoice.student_id.in_(ids),
            )
            .returning(StudentFeeChoice.student_id)
        ).all()
        changed = len(removed)
    _audit(session, ctx, item, {"term": term.id, "selected": selected, "students": len(ids), "changed": changed})
    session.commit()
    return int(changed)


# --- Discounts -------------------------------------------------------------------------------------


def list_discounts(session: Session) -> list[Discount]:
    return list(session.scalars(select(Discount).order_by(Discount.is_active.desc(), Discount.name)))


def _check_discount(kind: DiscountKind, value: int, fee_item_id: uuid.UUID | None, session: Session) -> None:
    if kind == DiscountKind.PERCENT and not 0 < value <= 10_000:
        raise DomainError("A percentage discount must be between 0.01% and 100%.", field="value")
    if kind == DiscountKind.FIXED and not 0 < value <= MAX_AMOUNT_KOBO:
        raise DomainError("Enter an amount more than ₦0.", field="value")
    if fee_item_id is not None:
        get_or_404(session, FeeItem, fee_item_id, label="Fee", field="fee_item_id")


def create_discount(
    session: Session,
    ctx: StaffContext,
    *,
    name: str,
    kind: DiscountKind,
    value: int,
    rule: DiscountRule,
    fee_item_id: uuid.UUID | None,
) -> Discount:
    _check_discount(kind, value, fee_item_id, session)
    discount = Discount(id=uuid.uuid4(), name=name.strip(), kind=kind, value=value, rule=rule, fee_item_id=fee_item_id)
    session.add(discount)
    _flush_unique(session, f"There's already a discount called {name.strip()}.", "name")
    _audit(
        session,
        ctx,
        discount,
        {"created": discount.name, "kind": kind, "value": value, "rule": rule, "fee_item": fee_item_id},
    )
    session.commit()
    return discount


def update_discount(
    session: Session,
    ctx: StaffContext,
    discount_id: uuid.UUID,
    *,
    name: str | None,
    value: int | None,
    is_active: bool | None,
) -> Discount:
    discount = get_or_404(session, Discount, discount_id, label="Discount", lock=True)
    before = {"name": discount.name, "value": discount.value, "is_active": discount.is_active}
    if value is not None:
        _check_discount(discount.kind, value, None, session)
        discount.value = value
    if name is not None:
        discount.name = name.strip()
    if is_active is not None:
        discount.is_active = is_active
    _flush_unique(session, "Another discount already has that name.", "name")
    diff = changed_fields(before, {"name": discount.name, "value": discount.value, "is_active": discount.is_active})
    if diff:
        _audit(session, ctx, discount, diff)
    session.commit()
    return discount


def student_discounts(session: Session, student_id: uuid.UUID) -> list[Discount]:
    return list(
        session.scalars(
            select(Discount)
            .join(
                StudentDiscount,
                and_(StudentDiscount.discount_id == Discount.id, StudentDiscount.student_id == student_id),
            )
            .order_by(Discount.name)
        )
    )


def give_discount(session: Session, ctx: StaffContext, student_id: uuid.UUID, discount_id: uuid.UUID) -> None:
    student = get_or_404(session, Student, student_id, label="Student")
    discount = get_or_404(session, Discount, discount_id, label="Discount", field="discount_id")
    if discount.rule != DiscountRule.MANUAL:
        raise DomainError("Sibling discounts are applied automatically.", field="discount_id")
    session.execute(
        insert(StudentDiscount)
        .values(school_id=ctx.school_id, student_id=student.id, discount_id=discount.id)
        .on_conflict_do_nothing()
    )
    _audit(session, ctx, student, {"discount_given": discount.name})
    session.commit()


def take_discount(session: Session, ctx: StaffContext, student_id: uuid.UUID, discount_id: uuid.UUID) -> None:
    student = get_or_404(session, Student, student_id, label="Student")
    session.execute(
        delete(StudentDiscount).where(
            StudentDiscount.student_id == student.id, StudentDiscount.discount_id == discount_id
        )
    )
    _audit(session, ctx, student, {"discount_removed": discount_id})
    session.commit()
