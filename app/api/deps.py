"""Composition root: the only place that picks adapters for the ports."""

import os
from collections.abc import Mapping
from functools import lru_cache
from typing import Annotated

from fastapi import Depends

from app.adapters.clock import OffsetClock, SystemClock
from app.adapters.config.yaml_loader import load_client_config
from app.adapters.llm.fake_llm import FakeLLM
from app.adapters.llm.pydantic_ai_llm import PydanticAILLM, openai_model
from app.adapters.persistence.sqlite_repo import SqliteCandidateRepository, create_sqlite_engine
from app.application.ports import Clock, LLMPort
from app.application.recruiter_service import RecruiterService
from app.application.screening_service import ScreeningService
from app.domain.models import ClientConfig


@lru_cache
def get_config() -> ClientConfig:
    return load_client_config(os.environ.get("CLIENT_ID", "grupo_sazon"))


@lru_cache
def get_repository() -> SqliteCandidateRepository:
    engine = create_sqlite_engine(os.environ.get("DATABASE_URL", "sqlite:///data/screening.db"))
    return SqliteCandidateRepository(engine)


def make_llm(environ: Mapping[str, str], config: ClientConfig) -> LLMPort:
    """An empty `LLM_MODEL` runs on the FakeLLM; `openai:<model>` on the real LLM."""
    llm_model = environ.get("LLM_MODEL", "")
    if not llm_model:
        return FakeLLM()
    provider, _, model_name = llm_model.partition(":")
    if provider != "openai" or not model_name:
        raise RuntimeError(f"LLM_MODEL must be openai:<model>, got {llm_model!r}")
    api_key = environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "LLM_MODEL is set but OPENAI_API_KEY is not: add the key to .env, "
            "or empty LLM_MODEL to run on the FakeLLM"
        )
    timeout = float(environ.get("LLM_TIMEOUT_SECONDS", "15"))
    return PydanticAILLM(openai_model(model_name, api_key), config, timeout)


@lru_cache
def get_llm() -> LLMPort:
    return make_llm(os.environ, get_config())


def dev_routes_enabled() -> bool:
    return os.environ.get("DEV_ROUTES", "").lower() in {"1", "true"}


@lru_cache
def get_dev_clock() -> OffsetClock:
    return OffsetClock()


@lru_cache
def get_clock() -> Clock:
    """With the dev routes, the clock the dev route moves forward."""
    return get_dev_clock() if dev_routes_enabled() else SystemClock()


@lru_cache
def get_screening_service() -> ScreeningService:
    return ScreeningService(
        config=get_config(),
        llm=get_llm(),
        repo=get_repository(),
        clock=get_clock(),
    )


@lru_cache
def get_recruiter_service() -> RecruiterService:
    return RecruiterService(
        config=get_config(), llm=get_llm(), repo=get_repository(), clock=get_clock()
    )


Screening = Annotated[ScreeningService, Depends(get_screening_service)]
Recruiter = Annotated[RecruiterService, Depends(get_recruiter_service)]
DevClock = Annotated[OffsetClock, Depends(get_dev_clock)]
