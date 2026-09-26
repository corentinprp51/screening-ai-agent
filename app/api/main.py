import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import json_routes, pages
from app.api.deps import get_screening_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_screening_service()  # load the config and create the tables at startup
    yield


logging.basicConfig()
logging.getLogger("app").setLevel(logging.INFO)  # the token usage of each LLM call

app = FastAPI(title="Candidate Screening", lifespan=lifespan)
app.include_router(json_routes.router)
app.include_router(pages.router)
