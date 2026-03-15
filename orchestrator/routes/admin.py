"""Admin endpoints: /health, /permissions, /review-queue."""

from fastapi import APIRouter
from fastapi import HTTPException
from permissions import get_all_permissions
from permissions import set_connector_mode
from pydantic import BaseModel

router = APIRouter()


class PermissionUpdate(BaseModel):
    mode: str


@router.get("/permissions")
def list_permissions():
    return get_all_permissions()


@router.put("/permissions/{connector}")
def update_permission(connector: str, body: PermissionUpdate):
    try:
        set_connector_mode(connector, body.mode.upper())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"connector": connector, "mode": body.mode.upper()}
