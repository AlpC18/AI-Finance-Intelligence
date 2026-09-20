"""Self-service consumer API key portal; key material is shown only once."""
from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.auth import get_current_user
from app.core.deps import get_consumer_service
from app.db.database import get_session
from app.models.consumer import ApiKeyCreate, ApiKeyCreated, ApiKeyRead
from app.models.user import User
from app.services.consumer_service import ConsumerService

router = APIRouter(prefix="/api/developer", tags=["developer"])


@router.post("/keys", response_model=ApiKeyCreated, status_code=201)
def create_key(data: ApiKeyCreate, user: User = Depends(get_current_user),
               session: Session = Depends(get_session),
               service: ConsumerService = Depends(get_consumer_service)) -> ApiKeyCreated:
    return service.create(session, user.id, data)


@router.get("/keys", response_model=list[ApiKeyRead])
def list_keys(user: User = Depends(get_current_user), session: Session = Depends(get_session),
              service: ConsumerService = Depends(get_consumer_service)) -> list[ApiKeyRead]:
    return service.list(session, user.id)


@router.delete("/keys/{key_id}", status_code=204)
def revoke_key(key_id: int, user: User = Depends(get_current_user),
               session: Session = Depends(get_session),
               service: ConsumerService = Depends(get_consumer_service)) -> None:
    service.revoke(session, user.id, key_id)
