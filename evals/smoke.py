"""A smoke run against the real LLM: `task dev:smoke`, with `LLM_MODEL` and `OPENAI_API_KEY`
in `.env`. Scripted candidate messages go through the ScreeningService on an in-memory
database; each extraction, reply and token usage is printed. Not part of `task dev:test`:
it calls the API and its output varies. Read it by hand: colloquial years, platforms, a
relative date, a language switch and a bare number keeping the language."""

import logging
import os

from app.adapters.clock import SystemClock
from app.adapters.config.yaml_loader import load_client_config
from app.adapters.llm.fake_llm import FakeLLM
from app.adapters.persistence.sqlite_repo import SqliteCandidateRepository, create_sqlite_engine
from app.api.deps import make_llm
from app.application.ports import LLMPort
from app.application.screening_service import ScreeningService

# Each conversation is a phone number and the candidate's messages.
CONVERSATIONS = {
    # Colloquial answers: a moped license, a couple of years, platforms, a relative date.
    "600000001": [
        "hola! sí, adelante",
        "Me llamo Ana López",
        "sí, de moto",
        "sí, tengo una moto mía",
        "vivo en Getafe",
        "busco jornada completa, mejor por la noche",
        "un par de años en Glovo y Uber Eats",
        "puedo empezar el lunes",
        "sí, todo correcto",
    ],
    # A switch to English, then a bare number that must keep the conversation in English.
    "600000002": [
        "hola, sí",
        "Me llamo John Smith",
        "Sorry, can we do this in English? Yes, I have a car license",
        "yes, my own car",
        "I live in Chamberí, Madrid",
        "part time",
        "evenings",
        "2",
        "next week",
        "yes",
    ],
}


class PrintingLLM:
    """Prints each extraction on its way to the service."""

    def __init__(self, llm: LLMPort) -> None:
        self._llm = llm

    def extract(self, *args):
        extraction = self._llm.extract(*args)
        print(f"  extraction: {extraction.model_dump_json(exclude_none=True)}")
        return extraction

    def reply(self, *args):
        return self._llm.reply(*args)

    def summarize(self, *args):
        return self._llm.summarize(*args)


def main() -> None:
    logging.basicConfig(format="  %(message)s")
    logging.getLogger("app.adapters.llm.pydantic_ai_llm").setLevel(logging.INFO)
    config = load_client_config(os.environ.get("CLIENT_ID", "grupo_sazon"))
    llm = make_llm(os.environ, config)
    if isinstance(llm, FakeLLM):
        raise SystemExit("Set LLM_MODEL (and OPENAI_API_KEY) in .env to run the smoke test.")

    repo = SqliteCandidateRepository(create_sqlite_engine("sqlite://"))
    service = ScreeningService(config, PrintingLLM(llm), repo, SystemClock())
    for phone, messages in CONVERSATIONS.items():
        print(f"\n=== {phone}")
        service.apply(phone)
        print(f"agent> {service.transcript(phone)[0].content}")
        for text in messages:
            print(f"you> {text}")
            print(f"agent> {service.handle_message(phone, text)}")

        candidate = service.candidate(phone)
        print(f"\nstatus: {candidate.status}, score: {candidate.score.total}")
        print(f"flags: {candidate.state.all_flags()}")
        if candidate.summary:
            print(f"summary: {candidate.summary.text}")


if __name__ == "__main__":
    main()
