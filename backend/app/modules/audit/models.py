"""
Append-only audit trail. Rows are never updated or deleted: a database
trigger refuses both (see the migration), whatever the application does.

Two tables, so tenant scoping stays in one mechanism:
- `audit_log`: everything that happens inside a school (TenantMixin, scoped
  like any other school data). Platform admins acting on a school write here
  too, with that school's id.
- `platform_audit_log`: events with no school (platform admin sign-ins,
  school creation, failed sign-ins for unknown emails).
"""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from app.core.base import Base, UUIDPrimaryKey
from app.core.tenancy import TenantMixin


class AuditAction(enum.StrEnum):
    LOGIN_SUCCEEDED = "auth.login_succeeded"
    LOGIN_FAILED = "auth.login_failed"
    LOGOUT = "auth.logout"
    SESSION_REUSE_DETECTED = "auth.refresh_reuse_detected"
    PASSWORD_CHANGED = "auth.password_changed"
    PASSWORD_RESET_REQUESTED = "auth.password_reset_requested"
    PASSWORD_RESET_COMPLETED = "auth.password_reset_completed"
    MFA_ENABLED = "auth.mfa_enabled"
    MFA_DISABLED = "auth.mfa_disabled"
    MFA_RECOVERY_CODES_REGENERATED = "auth.mfa_recovery_codes_regenerated"
    MFA_RECOVERY_CODE_USED = "auth.mfa_recovery_code_used"
    STAFF_INVITED = "staff.invited"
    STAFF_UPDATED = "staff.updated"
    STAFF_DEACTIVATED = "staff.deactivated"
    STAFF_REACTIVATED = "staff.reactivated"
    STAFF_MFA_RESET = "staff.mfa_reset"
    SCHOOL_CREATED = "school.created"
    SCHOOL_UPDATED = "school.updated"
    SCHOOL_SUSPENDED = "school.suspended"
    SCHOOL_REACTIVATED = "school.reactivated"
    SCHOOL_SIGNED_UP = "school.signed_up"
    SCHOOL_ACTIVATED = "school.activated"
    SCHOOL_PLAN_CHANGED = "school.plan_changed"
    PLAN_SAVED = "platform.plan_saved"
    SUBSCRIPTIONS_GENERATED = "platform.subscriptions_generated"
    SUBSCRIPTION_PAYMENT_RECORDED = "platform.subscription_payment"
    SUBSCRIPTION_INVOICE_VOIDED = "platform.subscription_voided"
    SUBSCRIPTION_CHECKOUT_STARTED = "platform.subscription_checkout"
    SUBSCRIPTION_CHECKOUT_NEEDS_REVIEW = "platform.subscription_checkout_review"
    SUPPORT_LOOKUP = "platform.support_lookup"
    PRIVACY_EXPORTED = "privacy.exported"
    PRIVACY_ERASED = "privacy.erased"
    PRIVACY_RETENTION_APPLIED = "privacy.retention_applied"
    ACADEMIC_SESSION_CREATED = "calendar.session_created"
    ACADEMIC_CALENDAR_UPDATED = "calendar.updated"
    CURRENT_TERM_CHANGED = "calendar.current_term_changed"
    CLASS_STRUCTURE_CHANGED = "classes.changed"
    STUDENT_CREATED = "students.created"
    STUDENT_UPDATED = "students.updated"
    STUDENT_CLASS_CHANGED = "students.class_changed"
    STUDENT_WITHDRAWN = "students.withdrawn"
    STUDENT_REINSTATED = "students.reinstated"
    STUDENTS_IMPORTED = "students.imported"
    STUDENTS_ROLLED_FORWARD = "students.rolled_forward"
    GUARDIAN_LINKED = "guardians.linked"
    GUARDIAN_UNLINKED = "guardians.unlinked"
    GUARDIAN_CONTACT_CHANGED = "guardians.contact_changed"
    FEES_CHANGED = "fees.changed"
    INVOICES_GENERATED = "invoices.generated"
    INVOICE_CREATED = "invoices.created"
    INVOICE_ADJUSTED = "invoices.adjusted"
    INVOICE_VOIDED = "invoices.voided"
    PAYMENT_PLAN_CREATED = "invoices.plan_created"
    PAYMENT_PLAN_CANCELLED = "invoices.plan_cancelled"
    CLASS_CHARGE_ADDED = "invoices.class_charge_added"
    FEE_EXEMPTION_CHANGED = "invoices.exemption_changed"
    PUPIL_CHARGE_ADDED = "invoices.pupil_charge_added"
    PAYMENT_ACCOUNT_SET_UP = "payments.account_set_up"
    VIRTUAL_ACCOUNTS_ISSUED = "payments.virtual_accounts_issued"
    PAYMENT_RECORDED = "payments.recorded"
    PAYMENT_REVERSED = "payments.reversed"
    PAYMENT_MARKED_FOR_FEES = "payments.marked_for_fees"
    PAYMENT_NEEDS_REVIEW = "payments.needs_review"
    PAYMENT_LINK_CREATED = "payments.link_created"
    FAMILY_LINK_ISSUED = "payments.family_link_issued"
    PROVIDER_CHANGED = "platform.provider_changed"
    MANUAL_PAYMENT_RECORDED = "payments.manual_recorded"
    MANUAL_PAYMENT_APPROVED = "payments.manual_approved"
    MANUAL_PAYMENT_REJECTED = "payments.manual_rejected"
    PARENT_SIGNED_IN = "parents.signed_in"
    MESSAGING_CHANGED = "messaging.changed"
    REMINDERS_PREFERENCE_CHANGED = "messaging.reminders_preference"
    REMINDER_SENT_MANUALLY = "messaging.reminder_sent"
    REPORT_EXPORTED = "reports.exported"


class _AuditColumns(UUIDPrimaryKey):
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column()  # no FK: the trail outlives accounts
    # Snapshot of who acted, so the record stays meaningful if the account changes
    actor_label: Mapped[str] = mapped_column(String(255), default="")
    action: Mapped[str] = mapped_column(String(64))
    target_type: Mapped[str] = mapped_column(String(64), default="")
    target_id: Mapped[str] = mapped_column(String(64), default="")
    # Before/after values, passed through redaction before storage
    changes: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    ip_address: Mapped[str] = mapped_column(String(45), default="")
    user_agent: Mapped[str] = mapped_column(String(255), default="")
    request_id: Mapped[str] = mapped_column(String(64), default="")

    @declared_attr
    def created_at(cls) -> Mapped[datetime]:
        return mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditLog(TenantMixin, _AuditColumns, Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_school_created", "school_id", "created_at"),
        Index("ix_audit_log_school_target", "school_id", "target_type", "target_id"),
    )


class PlatformAuditLog(_AuditColumns, Base):
    __tablename__ = "platform_audit_log"
    __table_args__ = (Index("ix_platform_audit_log_created", "created_at"),)
