"""Embedded deal knowledge graph on SQLite (F-021/F-023/F-024).

Replaces the Neo4j dependency for DealForge's actual graph workload: a
handful of nodes and edges per deal (deal root, metrics, risks, companies),
written by agents and read back per deal. That workload needs no graph
server. SQLite ships with Python, needs no service, no credentials and
no network hop, and stores the graph in one file under ``DATA_DIR``.

Differences from the old Neo4j schema, both deliberate fixes:
- Nodes are scoped per deal: key ``(deal_id, label, name)``. Neo4j MERGEd
  on ``(label, name)`` globally, so one deal's ``financial_analyst_revenue``
  metric overwrote another deal's.
- Temporal validity (``valid_from`` / ``valid_until``) lives on the edge,
  as before, and ``query_current_facts`` honours it.

Config (env, optional):
  KG_SQLITE_PATH   database file (default ``$DATA_DIR/knowledge_graph.db``)
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import structlog

logger = structlog.get_logger(__name__)

VALID_LABELS = {"Company", "Risk", "Person", "Metric", "Product", "RegulatoryBody"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kg_deals (
    deal_id    TEXT PRIMARY KEY,
    name       TEXT,
    industry   TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kg_nodes (
    deal_id    TEXT NOT NULL,
    label      TEXT NOT NULL,
    name       TEXT NOT NULL,
    props      TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (deal_id, label, name)
);
CREATE TABLE IF NOT EXISTS kg_edges (
    deal_id     TEXT NOT NULL,
    rel         TEXT NOT NULL,
    label       TEXT NOT NULL,
    name        TEXT NOT NULL,
    valid_from  TEXT NOT NULL,
    valid_until TEXT,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (deal_id, rel, label, name)
);
CREATE INDEX IF NOT EXISTS kg_edges_deal ON kg_edges (deal_id, label);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).isoformat()


def _default_path() -> str:
    explicit = os.getenv("KG_SQLITE_PATH")
    if explicit:
        return explicit
    data_dir = os.getenv("DATA_DIR")
    if not data_dir:
        try:
            from app.config import get_settings

            data_dir = get_settings().DATA_DIR
        except Exception:
            data_dir = "data"
    return os.path.join(data_dir, "knowledge_graph.db")


class SQLiteGraphStore:
    """Thread-safe SQLite graph store; async methods run in a worker thread."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or _default_path()
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            if self.path != ":memory:":
                os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
            conn = sqlite3.connect(self.path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            if self.path != ":memory:":
                conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(_SCHEMA)
            self._conn = conn
        return self._conn

    def _run(self, fn, *args):
        with self._lock:
            conn = self._connection()
            try:
                result = fn(conn, *args)
                conn.commit()
                return result
            except Exception:
                conn.rollback()
                raise

    async def _arun(self, fn, *args):
        return await asyncio.to_thread(self._run, fn, *args)

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # ── writes ──────────────────────────────────────────────────────
    @staticmethod
    def _upsert_deal(conn, deal_id: str, name: Optional[str], industry: Optional[str]) -> None:
        now = _now()
        conn.execute(
            """
            INSERT INTO kg_deals (deal_id, name, industry, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(deal_id) DO UPDATE SET
                name = COALESCE(excluded.name, kg_deals.name),
                industry = COALESCE(excluded.industry, kg_deals.industry),
                updated_at = excluded.updated_at
            """,
            (deal_id, name, industry, now, now),
        )

    @staticmethod
    def _upsert_node(conn, deal_id, label, name, props, rel, valid_from, valid_until) -> None:
        now = _now()
        row = conn.execute(
            "SELECT props FROM kg_nodes WHERE deal_id=? AND label=? AND name=?",
            (deal_id, label, name),
        ).fetchone()
        merged = json.loads(row["props"]) if row else {}
        merged.update(props)  # Neo4j `SET e += $props` semantics
        conn.execute(
            """
            INSERT INTO kg_nodes (deal_id, label, name, props, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(deal_id, label, name) DO UPDATE SET
                props = excluded.props, updated_at = excluded.updated_at
            """,
            (deal_id, label, name, json.dumps(merged, default=str), now),
        )
        conn.execute(
            """
            INSERT INTO kg_edges (deal_id, rel, label, name, valid_from, valid_until, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(deal_id, rel, label, name) DO UPDATE SET
                valid_from = COALESCE(?, kg_edges.valid_from),
                valid_until = excluded.valid_until,
                updated_at = excluded.updated_at
            """,
            (deal_id, rel, label, name, valid_from or now, valid_until, now, valid_from),
        )

    async def initialize_deal(self, deal_id: str, deal_name: str, industry: str) -> None:
        await self._arun(self._upsert_deal, deal_id, deal_name, industry)

    async def add_entity(
        self,
        deal_id: str,
        entity_name: str,
        entity_label: str,
        properties: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not entity_name:
            return
        label = str(entity_label or "").capitalize()
        label = label if label in VALID_LABELS else "Entity"
        props = dict(properties or {})
        valid_from = _iso(props.get("valid_from"))
        valid_until = _iso(props.get("valid_until"))

        def _write(conn):
            # Agents may write findings before the orchestrator created the
            # deal root; create it instead of silently dropping the write.
            self._upsert_deal(conn, deal_id, None, None)
            self._upsert_node(conn, deal_id, label, str(entity_name), props, "INVOLVES", valid_from, valid_until)

        await self._arun(_write)

    async def add_risk(
        self, deal_id: str, risk_name: str, severity: int, category: str, description: str = "",
    ) -> None:
        if not risk_name:
            return
        props = {"severity": severity, "category": category, "description": description}

        def _write(conn):
            self._upsert_deal(conn, deal_id, None, None)
            self._upsert_node(conn, deal_id, "Risk", str(risk_name), props, "HAS_RISK", None, None)

        await self._arun(_write)

    # ── reads ───────────────────────────────────────────────────────
    async def query_current_facts(self, deal_id: str, label: Optional[str] = None) -> List[Dict[str, Any]]:
        """Facts valid now (F-024): ``valid_from <= now < valid_until``."""
        now = _now()

        def _read(conn):
            sql = """
                SELECT n.label, n.name, n.props, e.rel, e.valid_from, e.valid_until
                FROM kg_edges e
                JOIN kg_nodes n ON n.deal_id = e.deal_id AND n.label = e.label AND n.name = e.name
                WHERE e.deal_id = ?
                  AND e.valid_from <= ?
                  AND (e.valid_until IS NULL OR e.valid_until > ?)
            """
            params: List[Any] = [deal_id, now, now]
            if label:
                sql += " AND n.label = ?"
                params.append(label)
            sql += " ORDER BY n.label, n.name"
            return [
                {
                    "name": r["name"],
                    "labels": [r["label"]],
                    "relationship": r["rel"],
                    "properties": json.loads(r["props"]),
                    "since": r["valid_from"],
                    "until": r["valid_until"],
                }
                for r in conn.execute(sql, params).fetchall()
            ]

        return await self._arun(_read)

    async def get_risks(self, deal_id: str, min_severity: int = 0) -> List[Dict[str, Any]]:
        facts = await self.query_current_facts(deal_id, "Risk")
        risks = [
            {"name": f["name"], **f["properties"]}
            for f in facts
            if _as_number(f["properties"].get("severity")) >= min_severity
        ]
        return sorted(risks, key=lambda r: _as_number(r.get("severity")), reverse=True)

    async def get_deal(self, deal_id: str) -> Optional[Dict[str, Any]]:
        def _read(conn):
            row = conn.execute("SELECT * FROM kg_deals WHERE deal_id = ?", (deal_id,)).fetchone()
            return dict(row) if row else None

        return await self._arun(_read)

    async def deal_summary(self, deal_id: str) -> Dict[str, Any]:
        """Compact graph snapshot for prompts/APIs: deal, counts by label, top risks."""
        deal = await self.get_deal(deal_id)
        facts = await self.query_current_facts(deal_id)
        counts: Dict[str, int] = {}
        for f in facts:
            counts[f["labels"][0]] = counts.get(f["labels"][0], 0) + 1
        return {
            "deal": deal,
            "counts": counts,
            "top_risks": (await self.get_risks(deal_id))[:10],
            "facts": facts,
        }


def _as_number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


class NullGraphStore:
    """No-op store for ``KG_BACKEND=off``."""

    async def initialize_deal(self, *args, **kwargs) -> None:
        return None

    async def add_entity(self, *args, **kwargs) -> None:
        return None

    async def add_risk(self, *args, **kwargs) -> None:
        return None

    async def query_current_facts(self, *args, **kwargs) -> List[Dict[str, Any]]:
        return []

    async def get_risks(self, *args, **kwargs) -> List[Dict[str, Any]]:
        return []

    async def get_deal(self, *args, **kwargs) -> Optional[Dict[str, Any]]:
        return None

    async def deal_summary(self, deal_id: str) -> Dict[str, Any]:
        return {"deal": None, "counts": {}, "top_risks": [], "facts": []}
