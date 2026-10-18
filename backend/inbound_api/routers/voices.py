from fastapi import APIRouter, Depends
from inbound_core.languages import DEFAULT_LOCALE, Locale

from inbound_api.auth import current_user
from inbound_api.models import User
from inbound_api.schemas import VoiceCatalogueResponse
from inbound_api.services.voice_catalogue import voice_catalogue
from inbound_api.settings import get_settings

router = APIRouter(tags=["voices"])
_current_user = Depends(current_user)


@router.get("/voices", response_model=VoiceCatalogueResponse)
async def list_voices(
    locale: Locale = DEFAULT_LOCALE,
    _: User = _current_user,
) -> VoiceCatalogueResponse:
    key = get_settings().speechify_api_key
    api_key = key.get_secret_value().strip() if key is not None else None
    return await voice_catalogue.get(locale, api_key or None)
