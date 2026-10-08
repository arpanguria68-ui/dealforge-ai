"""Optional Neo4j backend for the deal knowledge graph (``KG_BACKEND=neo4j``).

The default backend is the embedded SQLite store in ``graph_store.py``; this
module is kept for deployments that already run Neo4j. The ``neo4j`` driver
is imported lazily, so it is not a hard dependency
(``pip install neo4j`` only when using this backend).
"""
import os
import time
from typing import Any, Dict, List, Optional
import structlog

logger = structlog.get_logger(__name__)

# After a failed connect, wait this long before trying again instead of
# re-dialling Neo4j on every single write.
_RECONNECT_BACKOFF_SECONDS = 60.0


class Neo4jClient:
    """Core client for Neo4j operations with connection pooling and error handling."""

    def __init__(self, uri: Optional[str] = None, user: Optional[str] = None, password: Optional[str] = None):
        self.uri = uri or os.getenv("NEO4J_URI", "bolt://localhost:7687")
        self.user = user or os.getenv("NEO4J_USER", "neo4j")
        self.password = password or os.getenv("NEO4J_PASSWORD", "")
        self.driver = None
        self._connected = False
        self._retry_after = 0.0

    async def connect(self):
        """Establish connection to Neo4j."""
        if self._connected or time.monotonic() < self._retry_after:
            return

        try:
            from neo4j import AsyncGraphDatabase

            self.driver = AsyncGraphDatabase.driver(
                self.uri, 
                auth=(self.user, self.password)
            )
            # Verify connectivity
            await self.driver.verify_connectivity()
            self._connected = True
            logger.info("neo4j_connected", uri=self.uri)
        except Exception as e:
            logger.warning("neo4j_connection_failed", error=str(e), uri=self.uri)
            self._connected = False
            self.driver = None
            self._retry_after = time.monotonic() + _RECONNECT_BACKOFF_SECONDS

    async def close(self):
        """Close Neo4j connection."""
        if self.driver:
            await self.driver.close()
            self._connected = False

    async def run_query(self, query: str, parameters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Execute a Cypher query and return results as a list of dictionaries."""
        if not self._connected:
            await self.connect()
            
        if not self._connected:
            logger.debug("neo4j_skipping_query_offline", query=query)
            return []

        async with self.driver.session() as session:
            try:
                result = await session.run(query, parameters or {})
                records = await result.data()
                return records
            except Exception as e:
                logger.error("neo4j_execution_error", error=str(e))
                return []

class Neo4jGraphStore:
    """Neo4j implementation of the deal graph store interface (F-021)."""

    def __init__(self, client: Neo4jClient):
        self.client = client

    async def initialize_deal(self, deal_id: str, deal_name: str, industry: str):
        """Create or update the root DEAL node (F-021)."""
        query = """
        MERGE (d:Deal {id: $deal_id})
        SET d.name = $name, 
            d.industry = $industry,
            d.updated_at = datetime(),
            d.created_at = coalesce(d.created_at, datetime())
        RETURN d
        """
        await self.client.run_query(query, {
            "deal_id": deal_id,
            "name": deal_name,
            "industry": industry
        })

    async def add_entity(self, deal_id: str, entity_name: str, entity_label: str, properties: Optional[Dict[str, Any]] = None):
        """Create a generic entity node and link it to the deal (F-021/F-023)."""
        # dynamic label injection requires careful handling or multiple MERGE blocks
        # we'll use a safer approach with MERGE on a base Entity label and then SET specific label if needed
        # but Neo4j MERGE doesn't support parameterizing labels directly in MERGE.
        # We will use apoc.merge.node if available, otherwise fixed categories.
        
        valid_labels = {"Company", "Risk", "Person", "Metric", "Product", "RegulatoryBody"}
        label = entity_label.capitalize() if entity_label.capitalize() in valid_labels else "Entity"
        
        query = f"""
        MATCH (d:Deal {{id: $deal_id}})
        MERGE (e:{label} {{name: $name}})
        SET e += $props,
            e.updated_at = datetime()
        MERGE (d)-[r:INVOLVES]->(e)
        SET r.updated_at = datetime(),
            r.valid_from = coalesce($valid_from, datetime(), r.valid_from),
            r.valid_until = $valid_until
        RETURN e
        """
        await self.client.run_query(query, {
            "deal_id": deal_id,
            "name": entity_name,
            "props": properties or {},
            "valid_from": (properties or {}).get("valid_from"),
            "valid_until": (properties or {}).get("valid_until")
        })

    async def query_current_facts(self, deal_id: str, label: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return only facts valid at the current time (F-024)."""
        label_filter = f":{label}" if label else ""
        query = f"""
        MATCH (d:Deal {{id: $deal_id}})-[r:INVOLVES]->(e{label_filter})
        WHERE (r.valid_until IS NULL OR r.valid_until > datetime())
          AND r.valid_from <= datetime()
        RETURN e.name as name, labels(e) as labels, e as properties, r.valid_from as since
        """
        return await self.client.run_query(query, {"deal_id": deal_id})

    async def add_risk(self, deal_id: str, risk_name: str, severity: int, category: str, description: str = ""):
        """Specialized method for adding risks with severity levels (F-021)."""
        query = """
        MATCH (d:Deal {id: $deal_id})
        MERGE (r:Risk {name: $name})
        SET r.severity = $severity,
            r.category = $category,
            r.description = $description,
            r.updated_at = datetime()
        MERGE (d)-[:HAS_RISK]->(r)
        RETURN r
        """
        await self.client.run_query(query, {
            "deal_id": deal_id,
            "name": risk_name,
            "severity": severity,
            "category": category,
            "description": description
        })

    async def get_risks(self, deal_id: str, min_severity: int = 0) -> List[Dict[str, Any]]:
        query = """
        MATCH (d:Deal {id: $deal_id})-[:HAS_RISK]->(r:Risk)
        WHERE coalesce(r.severity, 0) >= $min_severity
        RETURN r.name AS name, r.severity AS severity, r.category AS category,
               r.description AS description
        ORDER BY r.severity DESC
        """
        return await self.client.run_query(query, {"deal_id": deal_id, "min_severity": min_severity})

    async def get_deal(self, deal_id: str) -> Optional[Dict[str, Any]]:
        rows = await self.client.run_query(
            "MATCH (d:Deal {id: $deal_id}) RETURN d.id AS deal_id, d.name AS name, d.industry AS industry",
            {"deal_id": deal_id},
        )
        return rows[0] if rows else None

    async def deal_summary(self, deal_id: str) -> Dict[str, Any]:
        facts = await self.query_current_facts(deal_id)
        counts: Dict[str, int] = {}
        for f in facts:
            for label in f.get("labels") or ["Entity"]:
                counts[label] = counts.get(label, 0) + 1
        return {
            "deal": await self.get_deal(deal_id),
            "counts": counts,
            "top_risks": (await self.get_risks(deal_id))[:10],
            "facts": facts,
        }


# Backwards-compatible name for older imports.
DealKnowledgeGraph = Neo4jGraphStore
