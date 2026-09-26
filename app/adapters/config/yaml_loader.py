from pathlib import Path

import yaml

from app.domain.models import ClientConfig

CONFIG_DIR = Path(__file__).resolve().parents[3] / "config" / "clients"


def load_client_config(client_id: str, config_dir: Path = CONFIG_DIR) -> ClientConfig:
    data = yaml.safe_load((config_dir / f"{client_id}.yaml").read_text(encoding="utf-8"))
    return ClientConfig.model_validate({"client_id": client_id, **data})
