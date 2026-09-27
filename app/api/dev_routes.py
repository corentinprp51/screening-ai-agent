"""Dev-only routes, included when DEV_ROUTES is set: move the clock to demo the sweep."""

from datetime import datetime, timedelta

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.deps import DevClock, Screening

router = APIRouter(prefix="/api/dev")


class AdvanceIn(BaseModel):
    hours: float = Field(ge=0)


class ClockOut(BaseModel):
    now: datetime


@router.post("/tick")
def advance_and_tick(body: AdvanceIn, clock: DevClock, service: Screening) -> ClockOut:
    """Move the clock forward by `hours`, then run the sweep (Nudges, deadline, scores)."""
    clock.advance(timedelta(hours=body.hours))
    service.tick()
    return ClockOut(now=clock.now())
