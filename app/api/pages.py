from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.api.deps import Screening
from app.application.screening_service import UnknownCandidate

router = APIRouter(default_response_class=HTMLResponse)
templates = Jinja2Templates(directory=Path(__file__).resolve().parents[1] / "web" / "templates")


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
        {"candidate": candidate, "messages": service.transcript(handle)},
    )


@router.post("/chat/{handle}/messages")
def chat_message(request: Request, handle: str, service: Screening, text: Annotated[str, Form()]):
    """HTMX: returns the candidate's bubble and the agent's reply bubble."""
    try:
        reply = service.handle_message(handle, text)
    except UnknownCandidate:
        return HTMLResponse(status_code=404)
    return templates.TemplateResponse(
        request,
        "partials/bubbles.html",
        {"messages": [{"role": "candidate", "content": text}, {"role": "agent", "content": reply}]},
    )
