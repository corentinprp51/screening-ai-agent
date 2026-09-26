import pytest
import yaml

from app.adapters.config.yaml_loader import CONFIG_DIR, load_client_config


def test_a_knock_out_flag_on_a_field_type_without_a_rule_fails_at_load(tmp_path):
    data = yaml.safe_load((CONFIG_DIR / "grupo_sazon.yaml").read_text(encoding="utf-8"))
    data["fields"][0] = {"type": "name", "knock_out": True}
    (tmp_path / "bad.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")

    with pytest.raises(ValueError, match="name"):
        load_client_config("bad", config_dir=tmp_path)
