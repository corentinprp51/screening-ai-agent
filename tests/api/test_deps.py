import pytest

from app.adapters.config.yaml_loader import load_client_config
from app.adapters.llm.fake_llm import FakeLLM
from app.adapters.llm.pydantic_ai_llm import PydanticAILLM
from app.api.deps import make_llm

CONFIG = load_client_config("grupo_sazon")


def test_no_llm_model_runs_on_the_fake_llm():
    assert isinstance(make_llm({"LLM_MODEL": ""}, CONFIG), FakeLLM)
    assert isinstance(make_llm({}, CONFIG), FakeLLM)


def test_an_llm_model_with_a_key_runs_on_the_pydantic_ai_adapter():
    environ = {"LLM_MODEL": "openai:gpt-6-luna", "OPENAI_API_KEY": "sk-test"}

    assert isinstance(make_llm(environ, CONFIG), PydanticAILLM)


def test_an_llm_model_without_a_key_fails_with_a_clear_error():
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        make_llm({"LLM_MODEL": "openai:gpt-6-luna"}, CONFIG)
