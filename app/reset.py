"""Delete every candidate, message, event and consent drop-off: `task dev:reset`."""

from app.api.deps import get_recruiter_service


def main() -> None:
    get_recruiter_service().reset()
    print("Reset: no candidates left.")


if __name__ == "__main__":
    main()
