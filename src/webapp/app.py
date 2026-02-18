"""FastAPI application factory."""
from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="Sharing Audit Dashboard", docs_url="/api/docs")

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    return app
