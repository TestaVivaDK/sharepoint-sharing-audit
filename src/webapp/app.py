"""FastAPI application factory."""

from fastapi import FastAPI
from shared.config import WebappConfig
from webapp.auth import SessionStore
from webapp.routes_auth import router as auth_router


def create_app() -> FastAPI:
    app = FastAPI(title="Sharing Audit Dashboard", docs_url="/api/docs")

    config = WebappConfig()
    app.state.config = config
    app.state.sessions = SessionStore()

    app.include_router(auth_router)

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    return app
