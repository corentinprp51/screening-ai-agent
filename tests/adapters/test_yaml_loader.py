import pytest
import yaml

from app.adapters.config.yaml_loader import CONFIG_DIR, load_client_config


def test_a_knock_out_flag_on_a_field_type_without_a_rule_fails_at_load(tmp_path):
    data = yaml.safe_load((CONFIG_DIR / "grupo_sazon.yaml").read_text(encoding="utf-8"))
    data["fields"][0] = {"type": "name", "knock_out": True}
    (tmp_path / "bad.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")

    with pytest.raises(ValueError, match="name"):
        load_client_config("bad", config_dir=tmp_path)


def test_the_client_platforms_are_loaded():
    config = load_client_config("grupo_sazon")

    assert config.platforms == ["Glovo", "Uber Eats", "Just Eat", "Rappi", "Didi Food"]


def _write_config(tmp_path, change):
    data = yaml.safe_load((CONFIG_DIR / "grupo_sazon.yaml").read_text(encoding="utf-8"))
    change(data)
    (tmp_path / "bad.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


def test_a_config_without_nudge_templates_fails_at_load(tmp_path):
    _write_config(tmp_path, lambda data: data["templates"].pop("nudges"))

    with pytest.raises(ValueError, match="nudges"):
        load_client_config("bad", config_dir=tmp_path)


def test_a_config_with_a_nudge_template_missing_fails_at_load(tmp_path):
    _write_config(tmp_path, lambda data: data["templates"]["nudges"]["en"].pop())

    with pytest.raises(ValueError, match="one template per nudge delay"):
        load_client_config("bad", config_dir=tmp_path)


def test_a_config_without_the_nudges_of_a_language_fails_at_load(tmp_path):
    _write_config(tmp_path, lambda data: data["templates"]["nudges"].pop("en"))

    with pytest.raises(ValueError, match="every language"):
        load_client_config("bad", config_dir=tmp_path)


def test_a_nudge_delay_after_the_deadline_fails_at_load(tmp_path):
    _write_config(tmp_path, lambda data: data.update(nudge_delays_hours=[1, 20, 80]))

    with pytest.raises(ValueError, match="nudge delays"):
        load_client_config("bad", config_dir=tmp_path)


def test_a_nudge_names_the_candidate_and_the_questions_left():
    config = load_client_config("grupo_sazon")

    assert config.nudge("es", 1, "Ana", 3) == (
        "¿Seguimos, Ana? Ya casi está, preguntas pendientes: 3."
    )
    assert config.nudge("en", 1, None, 3) == ("Shall we carry on? Almost done, questions left: 3.")


def test_the_impact_baselines_are_loaded():
    impact = load_client_config("grupo_sazon").impact

    assert (impact.call_minutes, impact.unanswered_calls_per_candidate) == (15, 1.5)
    assert impact.recruiter_hourly_cost == 20
    assert (
        impact.llm_price_per_million_tokens.input,
        impact.llm_price_per_million_tokens.output,
    ) == (
        0.4,
        1.6,
    )
    assert (impact.no_answer_rate, impact.unqualified_time_share) == (0.6, 0.8)
    assert impact.targets.completion_rate == 0.6


def test_a_config_without_the_impact_baselines_fails_at_load(tmp_path):
    _write_config(tmp_path, lambda data: data.pop("impact"))

    with pytest.raises(ValueError, match="impact"):
        load_client_config("bad", config_dir=tmp_path)
