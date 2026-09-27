"""Delete every candidate, message, event and consent drop-off: `task dev:reset`."""

from app.api.deps import get_repository


def main() -> None:
    get_repository().reset()  # the repository alone: a reset never needs the LLM config
    print("Reset: no candidates left.")


if __name__ == "__main__":
    main()
