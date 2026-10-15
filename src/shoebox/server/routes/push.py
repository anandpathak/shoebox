"""Turn notifications on for a device."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import push
from ..deps import user

router = APIRouter(prefix="/api/push", dependencies=[Depends(user)])


class Subscribe(BaseModel):
    subscription: dict
    device_id: str | None = Field(None, max_length=64)


class Unsubscribe(BaseModel):
    endpoint: str


@router.get("/key")
def public_key():
    return {"publicKey": push.keys()["public_key"]}


@router.post("/subscribe")
def subscribe(body: Subscribe):
    try:
        return {"subscriptions": push.subscribe(body.subscription, body.device_id)}
    except ValueError as e:
        raise HTTPException(422, str(e)) from None


@router.post("/unsubscribe")
def unsubscribe(body: Unsubscribe):
    push.unsubscribe(body.endpoint)
    return {"ok": True}


@router.post("/test")
def test_notification():
    return {"sent": push.notify("Shoebox", "Notifications are on for this device.")}
