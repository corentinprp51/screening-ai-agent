"""Fill the dashboard with demo candidates: `task dev:seed`, with `LLM_MODEL` and
`OPENAI_API_KEY` in `.env`. It first deletes every candidate, as `task dev:reset` does.

Each seed is a scripted conversation played through the ScreeningService against the real
LLM, on a fixed clock set hours or days back, so the queue shows real replies and summaries
and the Impact tab has figures over time. The LLM's reading varies, so the printed status
of a seed may differ from its comment."""

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.adapters.clock import FixedClock
from app.adapters.llm.fake_llm import FakeLLM
from app.api.deps import get_config, get_repository, make_llm
from app.application.recruiter_service import RecruiterService
from app.application.screening_service import ScreeningService

MESSAGE_GAP = timedelta(minutes=1)  # between two messages of a conversation
DECISION_DELAY = timedelta(hours=2)  # before a recruiter confirms a proposed rejection


@dataclass(frozen=True)
class Seed:
    phone: str
    hours_ago: float  # when the candidate applied
    messages: list[str]
    silent: bool = False  # then silent: ticked through the Nudges to the deadline
    confirm_rejection: bool = False  # a recruiter confirms the proposed rejection


# Silent seeds apply days back, so their ticks end before any other seed applied: a tick
# sweeps every open candidate, and the others' questions are still in its future.
SILENT_SEEDS = [
    # Abandoned at the availability question.
    Seed(
        "34600200001",
        120,
        ["hola, sí", "Diego Flores", "sí, de moto", "sí, moto propia", "vivo en Monterrey"],
        silent=True,
    ),
    # Never answers the greeting: erased at the deadline, counted as a consent drop-off.
    Seed("34600200002", 144, [], silent=True),
]

SEEDS = [
    # Qualified, Spain.
    Seed(
        "34600200003",
        26,
        [
            "hola! sí, adelante",
            "Me llamo Lucas Martín",
            "sí, carnet de coche",
            "sí, tengo coche propio",
            "vivo en Chamberí",
            "jornada completa",
            "por la noche",
            "4 años en Glovo",
            "puedo empezar ya",
            "sí, todo correcto",
        ],
    ),
    # Qualified, Mexico.
    Seed(
        "34600200004",
        20,
        [
            "sí claro",
            "Daniela Hernández",
            "sí, licencia de motoneta",
            "sí, es mía",
            "en Coyoacán, Ciudad de México",
            "solo fines de semana",
            "me da igual el horario",
            "un año en Rappi y Didi Food",
            "la próxima semana",
            "sí",
        ],
    ),
    # Qualified, in English.
    Seed(
        "34600200005",
        6,
        [
            "Hi, yes let's go",
            "Tom Baker",
            "yes, a car license",
            "yes, my own car",
            "I live in Gràcia, Barcelona",
            "part time and weekends",
            "afternoons",
            "no experience yet",
            "in two weeks",
            "yes, all good",
        ],
    ),
    # Qualified to review: a shared vehicle.
    Seed(
        "34600200006",
        30,
        [
            "sí",
            "Sofía Ramírez",
            "sí, de moto",
            "uso la moto de mi hermano",
            "la compartimos, a veces la usa él",
            "vivo en Zapopan",
            "tiempo completo",
            "en la tarde",
            "2 años en Uber Eats",
            "el lunes",
            "sí, correcto",
        ],
    ),
    # Rejection proposed: outside the service area.
    Seed(
        "34600200007",
        3,
        ["vale, sí", "Pablo Navarro", "sí, de coche", "sí, coche propio", "vivo en Zaragoza"],
    ),
    # Rejected: no own vehicle, confirmed by a recruiter.
    Seed(
        "34600200008",
        40,
        ["sí", "Marcos Díaz", "sí, tengo carnet de coche", "no, no tengo vehículo"],
        confirm_rejection=True,
    ),
    # Withdrawn.
    Seed("34600200009", 10, ["sí", "Elena Castro", "la verdad ya no me interesa, gracias"]),
    # In progress: stopped at the schedule question.
    Seed(
        "34600200010",
        0.5,
        [
            "sí, dime",
            "Carmen Ruiz",
            "sí, de coche",
            "sí, mío",
            "vivo en Triana, Sevilla",
            "media jornada",
        ],
    ),
]


def play(seed: Seed, now: datetime) -> str:
    config = get_config()
    repo = get_repository()
    llm = make_llm(os.environ, config)
    clock = FixedClock(now - timedelta(hours=seed.hours_ago))
    service = ScreeningService(config, llm, repo, clock)
    service.apply(seed.phone)
    for text in seed.messages:
        clock.set(clock.now() + MESSAGE_GAP)
        service.handle_message(seed.phone, text)
    if seed.silent:
        silent_since = clock.now()
        for hours in (*config.nudge_delays_hours, config.deadline_hours):
            clock.set(silent_since + timedelta(hours=hours, minutes=1))
            service.tick()
    candidate = service.candidate(seed.phone)
    if seed.confirm_rejection and candidate:
        clock.set(clock.now() + DECISION_DELAY)
        RecruiterService(config, llm, repo, clock).confirm_rejection(candidate.id)
        candidate = service.candidate(seed.phone)
    status = candidate.status.value if candidate else "erased (no consent)"
    return f"{seed.phone}: {status}"


def main() -> None:
    if isinstance(make_llm(os.environ, get_config()), FakeLLM):
        raise SystemExit("Set LLM_MODEL (and OPENAI_API_KEY) in .env to seed the dashboard.")
    get_repository().reset()
    now = datetime.now(UTC)
    for seed in SILENT_SEEDS:
        print(play(seed, now))
    with ThreadPoolExecutor(max_workers=len(SEEDS)) as pool:
        for line in pool.map(lambda seed: play(seed, now), SEEDS):
            print(line)


if __name__ == "__main__":
    main()
