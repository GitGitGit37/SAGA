from typing import Any

from sqlalchemy.orm import Session

from app.db.models import AuditLog


def record(
    db: Session,
    *,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: int,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    **details: Any,
) -> AuditLog:
    """Append an audit entry. Caller owns the transaction."""
    entry = AuditLog(
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before=before,
        after=after,
        details=details,
    )
    db.add(entry)
    return entry
