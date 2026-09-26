"""Composition root: the only place that picks adapters for the ports."""

import os
from functools import lru_cache
from typing import Annotated

from fastapi import Depends

from app.adapters.clock import SystemClock
from app.adapters.config.yaml_loader import load_client_config
from app.adapters.llm.fake_llm import FakeLLM
from app.adapters.persistence.sqlite_repo import SqliteCandidateRepository, create_sqlite_engine
from app.application.screening_service import ScreeningService


@lru_cache
def get_screening_service() -> ScreeningService:
    config = load_client_config(os.environ.get("CLIENT_ID", "grupo_sazon"))
    engine = create_sqlite_engine(os.environ.get("DATABASE_URL", "sqlite:///data/screening.db"))
    return ScreeningService(
        config=config,
        llm=FakeLLM(),
        repo=SqliteCandidateRepository(engine),
        clock=SystemClock(),
    )


Screening = Annotated[ScreeningService, Depends(get_screening_service)]
