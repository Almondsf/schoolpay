"""Imports every module's models so SQLAlchemy and Alembic see the whole schema."""


def load_all_models() -> None:
    # Imported for their side effect of registering tables on Base.metadata
    from app.core import outbox, sequences  # noqa: F401
    from app.modules.accounts import models as accounts  # noqa: F401
    from app.modules.audit import models as audit  # noqa: F401
    from app.modules.fees import models as fees  # noqa: F401
    from app.modules.notifications import models as notifications  # noqa: F401
    from app.modules.parents import models as parents  # noqa: F401
    from app.modules.payments import models as payments  # noqa: F401
    from app.modules.platform import models as platform  # noqa: F401
    from app.modules.receipts import models as receipts  # noqa: F401
    from app.modules.schools import models as schools  # noqa: F401
    from app.modules.students import models as students  # noqa: F401
