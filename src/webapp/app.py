"""FastAPI application factory."""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from shared.config import WebappConfig
from shared.neo4j_client import Neo4jClient
from webapp.auth import SessionStore
from webapp.routes_auth import router as auth_router
from webapp import routes_files


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = app.state.config
    neo4j = Neo4jClient(config.neo4j.uri, config.neo4j.user, config.neo4j.password)
    routes_files._neo4j = neo4j
    yield
    neo4j.close()
    routes_files._neo4j = None


def create_app() -> FastAPI:
    config = WebappConfig()

    app = FastAPI(title="Sharing Audit Dashboard", docs_url="/api/docs", lifespan=lifespan)
    app.state.config = config
    app.state.sessions = SessionStore()

    app.include_router(auth_router)
    app.include_router(routes_files.router)

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    return app
