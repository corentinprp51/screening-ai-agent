"""Terminal chat on the same ScreeningService: `uv run python -m app.cli`."""

from app.api.deps import get_screening_service
from app.domain.models import Status


def main() -> None:
    service = get_screening_service()
    candidate = service.apply(input("Phone number: "), input("Name (optional): "))
    for message in service.transcript(candidate.handle):
        print(f"{message.role}> {message.content}")

    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not text:
            continue
        print(f"agent> {service.handle_message(candidate.handle, text)}")
        candidate = service.candidate(candidate.handle)
        if candidate is None or candidate.status != Status.IN_PROGRESS:
            return


if __name__ == "__main__":
    main()
