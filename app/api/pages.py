from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.api.deps import Recruiter, Screening
from app.application.recruiter_service import (
    LLMUnavailable,
    NotAbandoned,
    NotRejectionProposed,
)
from app.application.screening_service import UnknownCandidate
from app.domain.models import Status

router = APIRouter(default_response_class=HTMLResponse)
templates = Jinja2Templates(directory=Path(__file__).resolve().parents[1] / "web" / "templates")

STATUS_LABELS = {
    Status.IN_PROGRESS: "In progress",
    Status.REJECTION_PROPOSED: "To confirm",
    Status.QUALIFIED: "Qualified",
    Status.QUALIFIED_TO_REVIEW: "Qualified to review",
    Status.REJECTED: "Rejected",
    Status.WITHDRAWN: "Withdrawn",
    Status.ABANDONED: "Abandoned",
}
templates.env.globals["status_labels"] = STATUS_LABELS


@router.get("/")
def home():
    return RedirectResponse("/chat")


@router.get("/chat")
def chat_start(request: Request):
    return templates.TemplateResponse(request, "chat_start.html")


@router.post("/chat")
def chat_apply(
    request: Request,
    service: Screening,
    phone: Annotated[str, Form()],
    name: Annotated[str, Form()] = "",
):
    try:
        candidate = service.apply(phone, name)
    except ValueError as error:
        return templates.TemplateResponse(
            request, "chat_start.html", {"error": str(error)}, status_code=422
        )
    return RedirectResponse(f"/chat/{candidate.handle}", status_code=303)


@router.get("/chat/{handle}")
def chat_page(request: Request, handle: str, service: Screening):
    candidate = service.candidate(handle)
    if candidate is None:
        return RedirectResponse("/chat")
    return templates.TemplateResponse(
        request,
        "chat.html",
        {
            "candidate": candidate,
            "persona": service.persona,
            "messages": service.transcript(handle),
        },
    )


@router.post("/chat/{handle}/messages")
def chat_message(request: Request, handle: str, service: Screening, text: Annotated[str, Form()]):
    """HTMX: returns only the agent's reply bubble; the candidate's was added on Send."""
    try:
        reply = service.handle_message(handle, text)
    except UnknownCandidate:
        return HTMLResponse(status_code=404)
    transcript = service.transcript(handle)
    # Consent declined: the candidate is erased with its messages, the reply is only shown.
    message = transcript[-1] if transcript else {"role": "agent", "content": reply}
    return templates.TemplateResponse(request, "partials/bubbles.html", {"messages": [message]})


@router.get("/chat/{handle}/messages")
def chat_messages(request: Request, handle: str, service: Screening, after: int = 0):
    """HTMX polling: the messages after the last one on screen, so messages sent by a
    recruiter action appear."""
    if service.candidate(handle) is None:
        return HTMLResponse(status_code=204)  # consent declined: keep what is on screen
    messages = service.transcript(handle, after)
    if not messages:
        return HTMLResponse(status_code=204)  # nothing new: no swap, no scroll
    return templates.TemplateResponse(request, "partials/bubbles.html", {"messages": messages})


@router.get("/dashboard")
def dashboard(request: Request, service: Recruiter, status: Status | None = None):
    return templates.TemplateResponse(
        request, "dashboard.html", {"rows": service.queue(status), "current": status}
    )


@router.get("/dashboard/impact")
def impact(request: Request, service: Recruiter):
    return templates.TemplateResponse(
        request, "impact.html", {"impact": service.impact(), "current": "impact"}
    )


@router.get("/dashboard/candidates/{candidate_id}")
def candidate_detail(request: Request, candidate_id: int, service: Recruiter):
    try:
        detail = service.detail(candidate_id)
    except UnknownCandidate:
        return HTMLResponse("Unknown candidate", status_code=404)
    return templates.TemplateResponse(request, "candidate.html", {"candidate": detail})


@router.post("/dashboard/candidates/{candidate_id}/reopen")
def candidate_reopen(candidate_id: int, service: Recruiter):
    """Reopen from the candidate page of an Abandoned candidate."""
    try:
        service.reopen(candidate_id)
    except UnknownCandidate:
        return HTMLResponse("Unknown candidate", status_code=404)
    except NotAbandoned:
        return HTMLResponse("The candidate is not Abandoned", status_code=409)
    return RedirectResponse(f"/dashboard/candidates/{candidate_id}", status_code=303)


@router.post("/dashboard/candidates/{candidate_id}/{decision}")
def candidate_decision(
    candidate_id: int,
    decision: Literal["confirm-rejection", "override-rejection"],
    service: Recruiter,
    back: Annotated[str, Form()] = "",
):
    """Confirm or Override from the "To confirm" tab or the candidate page."""
    action = (
        service.confirm_rejection if decision == "confirm-rejection" else service.override_rejection
    )
    try:
        action(candidate_id)
    except UnknownCandidate:
        return HTMLResponse("Unknown candidate", status_code=404)
    except NotRejectionProposed:
        return HTMLResponse("The candidate is not in Rejection proposed", status_code=409)
    except LLMUnavailable:
        return HTMLResponse("The message could not be written, try again", status_code=503)
    target = back if back.startswith("/dashboard") else f"/dashboard/candidates/{candidate_id}"
    return RedirectResponse(target, status_code=303)
