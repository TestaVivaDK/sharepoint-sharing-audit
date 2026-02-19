"""Environment-based configuration for all services."""

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()  # noqa: E402 — must run before dataclass defaults read os.environ


@dataclass(frozen=True)
class GraphApiConfig:
    tenant_id: str = field(default_factory=lambda: os.environ["TENANT_ID"])
    client_id: str = field(default_factory=lambda: os.environ["CLIENT_ID"])
    client_secret: str = field(default_factory=lambda: os.environ["CLIENT_SECRET"])


@dataclass(frozen=True)
class Neo4jConfig:
    uri: str = field(
        default_factory=lambda: os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    )
    user: str = field(default_factory=lambda: os.environ.get("NEO4J_USER", "neo4j"))
    password: str = field(default_factory=lambda: os.environ["NEO4J_PASSWORD"])


@dataclass(frozen=True)
class CollectorConfig:
    graph_api: GraphApiConfig = field(default_factory=GraphApiConfig)
    neo4j: Neo4jConfig = field(default_factory=Neo4jConfig)
    delay_ms: int = field(
        default_factory=lambda: int(os.environ.get("DELAY_MS", "100"))
    )


@dataclass(frozen=True)
class ReporterConfig:
    neo4j: Neo4jConfig = field(default_factory=Neo4jConfig)
    tenant_domain: str = field(
        default_factory=lambda: os.environ.get("TENANT_DOMAIN", "")
    )
    output_dir: str = field(
        default_factory=lambda: os.environ.get("REPORT_OUTPUT_DIR", "./reports")
    )
    webapp_url: str = field(default_factory=lambda: os.environ.get("WEBAPP_URL", ""))


@dataclass(frozen=True)
class WebappAuthConfig:
    """Auth config for the webapp — only needs tenant_id and client_id (no client_secret)."""

    tenant_id: str = field(default_factory=lambda: os.environ["TENANT_ID"])
    client_id: str = field(default_factory=lambda: os.environ["CLIENT_ID"])


@dataclass(frozen=True)
class WebappConfig:
    auth: WebappAuthConfig = field(default_factory=WebappAuthConfig)
    neo4j: Neo4jConfig = field(default_factory=Neo4jConfig)
    tenant_domain: str = field(
        default_factory=lambda: os.environ.get("TENANT_DOMAIN", "")
    )
