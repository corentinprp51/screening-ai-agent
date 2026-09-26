from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.api.deps import Screening
from app.application.screening_service import UnknownCandidate
from app.domain.models import Message, Status

router = APIRouter(prefix="/api")


class ApplicationIn(BaseModel):
    phone: str
    name: str | None = None


class MessageIn(BaseModel):
    text: str


class ReplyOut(BaseModel):
    reply: str


class TranscriptOut(BaseModel):
    handle: str
    status: Status
    stage: str
    messages: list[Message]


@router.post("/applications", status_code=201)
def create_application(body: ApplicationIn, service: Screening) -> TranscriptOut:
    try:
        candidate = service.apply(body.phone, body.name)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return get_transcript(candidate.handle, service)


@router.post("/screenings/{handle}/messages")
def post_message(handle: str, body: MessageIn, service: Screening) -> ReplyOut:
    try:
        return ReplyOut(reply=service.handle_message(handle, body.text))
    except UnknownCandidate as error:
        raise HTTPException(404, "Unknown candidate") from error


@router.get("/screenings/{handle}/transcript")
def get_transcript(handle: str, service: Screening) -> TranscriptOut:
    candidate = service.candidate(handle)
    if candidate is None:
        raise HTTPException(404, "Unknown candidate")
    return TranscriptOut(
        handle=candidate.handle,
        status=candidate.status,
        stage=candidate.state.stage,
        messages=service.transcript(handle),
    )
