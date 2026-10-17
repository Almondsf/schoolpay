"""Imports every module's models so SQLAlchemy and Alembic see the whole schema."""


def load_all_models() -> None:
    # Imported for their side effect of registering tables on Base.metadata
    from app.core import outbox  # noqa: F401
    from app.modules.accounts import models as accounts  # noqa: F401
    from app.modules.audit import models as audit  # noqa: F401
    from app.modules.schools import models as schools  # noqa: F401
    from app.modules.students import models as students  # noqa: F401
