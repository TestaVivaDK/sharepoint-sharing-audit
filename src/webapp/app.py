"""FastAPI application factory."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from shared.config import WebappConfig
from shared.neo4j_client import Neo4jClient
from webapp.auth import SessionStore
from webapp.routes_auth import router as auth_router
from webapp.routes_files import router as files_router
from webapp.routes_unshare import router as unshare_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = app.state.config
    neo4j = Neo4jClient(config.neo4j.uri, config.neo4j.user, config.neo4j.password)
    app.state.neo4j = neo4j
    yield
    neo4j.close()
    app.state.neo4j = None


def create_app() -> FastAPI:
    config = WebappConfig()

    app = FastAPI(title="Sharing Audit Dashboard", docs_url="/api/docs", lifespan=lifespan)
    app.state.config = config
    app.state.sessions = SessionStore()

    app.include_router(auth_router)
    app.include_router(files_router)
    app.include_router(unshare_router)

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    # Serve React SPA static files in production
    static_dir = Path(__file__).parent.parent.parent / "frontend" / "dist"
    if static_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(static_dir / "assets")), name="assets")

        @app.get("/{path:path}")
        def spa_fallback(path: str):
            file_path = (static_dir / path).resolve()
            if file_path.is_relative_to(static_dir) and file_path.exists() and file_path.is_file():
                return FileResponse(str(file_path))
            return FileResponse(str(static_dir / "index.html"))

    return app
