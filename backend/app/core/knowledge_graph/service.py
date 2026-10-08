"""Backend selection for the deal knowledge graph.

``KG_BACKEND`` (env): ``sqlite`` (default, embedded, no service),
``neo4j`` (needs a running Neo4j and ``pip install neo4j``) or ``off``.
"""

import os
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)

_graph: Optional[Any] = None


def build_knowledge_graph(backend: Optional[str] = None) -> Any:
    backend = (backend or os.getenv("KG_BACKEND", "sqlite")).strip().lower()
    if backend == "off":
        from app.core.knowledge_graph.graph_store import NullGraphStore

        return NullGraphStore()
    if backend == "neo4j":
        from app.core.knowledge_graph.neo4j_client import Neo4jClient, Neo4jGraphStore

        return Neo4jGraphStore(Neo4jClient())
    if backend != "sqlite":
        logger.warning("kg_backend_unknown_using_sqlite", backend=backend)
    from app.core.knowledge_graph.graph_store import SQLiteGraphStore

    return SQLiteGraphStore()


def get_knowledge_graph() -> Any:
    """Process-wide graph store (lazy: the SQLite file opens on first use)."""
    global _graph
    if _graph is None:
        _graph = build_knowledge_graph()
    return _graph
