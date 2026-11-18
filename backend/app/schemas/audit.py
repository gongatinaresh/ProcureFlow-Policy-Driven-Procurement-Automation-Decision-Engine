from datetime import datetime
from typing import Any

from app.schemas.common import ORMModel


class AuditEventResponse(ORMModel):
    id: int
    actor_id: str
    action: str
    entity_type: str
    entity_id: str
    reason: str | None
    before_state: dict[str, Any] | None
    after_state: dict[str, Any] | None
    created_at: datetime
