"""Asset-class discovery for dynamic clients and rule validation."""
from fastapi import APIRouter, Depends

from app.core.auth import get_current_user
from app.core.deps import get_asset_service
from app.models.assets import AssetProfile
from app.models.user import User
from app.services.asset_service import AssetService

router = APIRouter(prefix="/api/assets", tags=["assets"])


@router.get("/{symbol}/profile", response_model=AssetProfile)
def asset_profile(symbol: str, _: User = Depends(get_current_user),
                  service: AssetService = Depends(get_asset_service)) -> AssetProfile:
    return service.profile(symbol)
