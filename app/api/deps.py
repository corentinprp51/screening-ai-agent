"""Composition root: the only place that picks adapters for the ports."""

import os
from functools import lru_cache
from typing import Annotated

from fastapi import Depends

from app.adapters.clock import SystemClock
from app.adapters.config.yaml_loader import load_client_config
from app.adapters.llm.fake_llm import FakeLLM
from app.adapters.persistence.sqlite_repo import SqliteCandidateRepository, create_sqlite_engine
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


@lru_cache
def get_llm() -> FakeLLM:
    return FakeLLM()


@lru_cache
def get_screening_service() -> ScreeningService:
    return ScreeningService(
        config=get_config(),
        llm=get_llm(),
        repo=get_repository(),
        clock=SystemClock(),
    )


@lru_cache
def get_recruiter_service() -> RecruiterService:
    return RecruiterService(
        config=get_config(), llm=get_llm(), repo=get_repository(), clock=SystemClock()
    )


Screening = Annotated[ScreeningService, Depends(get_screening_service)]
Recruiter = Annotated[RecruiterService, Depends(get_recruiter_service)]
