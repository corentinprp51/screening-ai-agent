import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import dev_routes, json_routes, pages
from app.api.deps import dev_routes_enabled, get_screening_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_screening_service()  # load the config and create the tables at startup
    yield


logging.basicConfig()
logging.getLogger("app").setLevel(logging.INFO)  # the token usage of each LLM call


def create_app(with_dev_routes: bool) -> FastAPI:
    """`with_dev_routes` adds the routes that move the clock and reset the data, and the
    dashboard's Reset button; never on in production."""
    app = FastAPI(title="Candidate Screening", lifespan=lifespan)
    app.include_router(json_routes.router)
    app.include_router(pages.router)
    if with_dev_routes:
        app.include_router(dev_routes.router)
    app.state.dev_routes = with_dev_routes
    return app


app = create_app(dev_routes_enabled())
