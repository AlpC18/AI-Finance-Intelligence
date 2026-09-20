"""Paper-only scheduled investment and signal-rule endpoints."""
from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.auth import get_current_user
from app.core.deps import get_automation_service
from app.db.database import get_session
from app.models.automation import AutomationRuleCreate, AutomationRuleRead
from app.models.user import User
from app.services.automation_service import AutomationService

router = APIRouter(prefix="/api/automations", tags=["automations"])


@router.post("", response_model=AutomationRuleRead, status_code=201)
def create_rule(data: AutomationRuleCreate, user: User = Depends(get_current_user),
                session: Session = Depends(get_session),
                service: AutomationService = Depends(get_automation_service)) -> AutomationRuleRead:
    return service.create(session, user.id, data)


@router.get("", response_model=list[AutomationRuleRead])
def list_rules(user: User = Depends(get_current_user), session: Session = Depends(get_session),
               service: AutomationService = Depends(get_automation_service)) -> list[AutomationRuleRead]:
    return service.list(session, user.id)


@router.delete("/{rule_id}", status_code=204)
def delete_rule(rule_id: int, user: User = Depends(get_current_user),
                session: Session = Depends(get_session),
                service: AutomationService = Depends(get_automation_service)) -> None:
    service.delete(session, user.id, rule_id)
