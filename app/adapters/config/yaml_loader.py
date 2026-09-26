from pathlib import Path

import yaml

from app.domain.fields import KNOCK_OUTS
from app.domain.models import ClientConfig

CONFIG_DIR = Path(__file__).resolve().parents[3] / "config" / "clients"


def load_client_config(client_id: str, config_dir: Path = CONFIG_DIR) -> ClientConfig:
    data = yaml.safe_load((config_dir / f"{client_id}.yaml").read_text(encoding="utf-8"))
    config = ClientConfig.model_validate({"client_id": client_id, **data})
    for field_config in config.fields:
        if field_config.knock_out and field_config.type not in KNOCK_OUTS:
            raise ValueError(f"No knock-out rule for the field type {field_config.type!r}")
    return config
