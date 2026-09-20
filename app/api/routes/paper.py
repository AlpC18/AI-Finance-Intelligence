"""Paper-trading endpoints; intentionally isolated from real broker execution."""
from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.auth import get_current_user
from app.core.deps import get_paper_trading_service
from app.db.database import get_session
from app.models.paper import PaperAccountCreate, PaperAccountRead, PaperOrderRequest
from app.models.user import User
from app.services.paper_trading_service import PaperTradingService

router = APIRouter(prefix="/api/paper", tags=["paper-trading"])


@router.get("/account", response_model=PaperAccountRead)
def paper_account(user: User = Depends(get_current_user), session: Session = Depends(get_session),
                  service: PaperTradingService = Depends(get_paper_trading_service)) -> PaperAccountRead:
    return service.account(session, user.id)


@router.post("/account/reset", response_model=PaperAccountRead)
def reset_paper_account(data: PaperAccountCreate, user: User = Depends(get_current_user),
                        session: Session = Depends(get_session),
                        service: PaperTradingService = Depends(get_paper_trading_service)) -> PaperAccountRead:
    return service.reset(session, user.id, data)


@router.post("/orders", response_model=PaperAccountRead)
async def execute_paper_order(data: PaperOrderRequest, user: User = Depends(get_current_user),
                              session: Session = Depends(get_session),
                              service: PaperTradingService = Depends(get_paper_trading_service)) -> PaperAccountRead:
    return await service.execute(session, user.id, data)
