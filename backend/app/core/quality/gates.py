"""Per-stage Quality Gates for deal progression (F-027).

NOTE: this module must NOT import app.orchestrator at module load time —
app.orchestrator.graph imports QualityGate, so a top-level import back
would be circular (it broke direct ``import app.core.quality.gates``).
DealStage is imported lazily inside the methods that need it.
"""
from dataclasses import dataclass
from typing import List, Dict, Any, Optional, TYPE_CHECKING
import structlog

if TYPE_CHECKING:  # pragma: no cover
    from app.orchestrator.state import DealState, DealStage

logger = structlog.get_logger(__name__)

@dataclass
class QualityGate:
    """Thresholds for deal progression at various stages."""
    stage: Any
    max_critical_risks: int
    max_unresolved_conflicts: int
    min_consensus: float  # e.g., 0.8 = 80% agreement required

    @classmethod
    def for_stage(cls, stage: Any) -> "QualityGate":
        """Factory for stage-specific gates."""
        from app.orchestrator.state import DealStage

        gates = {
            DealStage.SCREENING: QualityGate(stage, max_critical_risks=3, max_unresolved_conflicts=2, min_consensus=0.5),
            DealStage.DUE_DILIGENCE: QualityGate(stage, max_critical_risks=2, max_unresolved_conflicts=1, min_consensus=0.7),
            DealStage.IC_MEMO: QualityGate(stage, max_critical_risks=1, max_unresolved_conflicts=0, min_consensus=0.9),
            DealStage.COMPLETED: QualityGate(stage, max_critical_risks=0, max_unresolved_conflicts=0, min_consensus=0.95),
        }
        # SCORING has no lenient fallback: post-scoring verification gets an
        # IC_MEMO-grade gate (previously it silently fell back to SCREENING).
        scoring = getattr(DealStage, "SCORING", None)
        if scoring is not None:
            gates.setdefault(
                scoring,
                QualityGate(scoring, max_critical_risks=1, max_unresolved_conflicts=0, min_consensus=0.8),
            )
        return gates.get(stage, gates[DealStage.SCREENING])

    def check(self, state: Any) -> Dict[str, Any]:
        """
        Apply gate logic to current state.
        Returns check results and pass/fail boolean.
        """
        # 1. Count critical risks (severity >= 9)
        # Assuming risks are stored in risk_output or a consolidated risks list
        risks = (state.get("risk_output") or {}).get("risks", [])
        critical_count = sum(1 for r in risks if r.get("severity", 0) >= 9)
        
        # 2. Check for unresolved conflicts (from debate node)
        unresolved_conflicts = (state.get("debate_output") or {}).get("unresolved_count", 0)
        
        # 3. Check consensus (e.g., from scoring node)
        consensus = state.get("consensus_score", 1.0)
        
        passed = (
            critical_count <= self.max_critical_risks and
            unresolved_conflicts <= self.max_unresolved_conflicts and
            consensus >= self.min_consensus
        )
        
        return {
            "passed": passed,
            "critical_count": critical_count,
            "critical_limit": self.max_critical_risks,
            "unresolved_conflicts": unresolved_conflicts,
            "consensus": consensus,
            "consensus_required": self.min_consensus,
            "stage": self.stage.value
        }

    @classmethod
    async def verify_stage(
        cls,
        stage: Any,
        data: Optional[Dict[str, Any]] = None,
        threshold: float = 0.8,
    ) -> Dict[str, Any]:
        """Async entry point used by the orchestrator.

        ``graph.py`` calls ``QualityGate.verify_stage(stage, data, threshold)``
        (this method was missing → AttributeError, silently swallowed, gate
        never enforced). Adapts the agent-output-shaped ``data`` dict into the
        state shape :meth:`check` expects, then maps the result onto the
        ``{blocked, reasons}`` contract the caller reads.
        """
        data = data or {}
        gate = cls.for_stage(stage)
        # check() is sync and reads risk_output/debate_output/consensus_score;
        # bridge common agent-output keys onto that shape.
        pseudo_state: Dict[str, Any] = {
            "risk_output": {"risks": data.get("risks", [])},
            "debate_output": {
                "unresolved_count": data.get(
                    "unresolved_count",
                    len(data.get("unresolved_issues", []) or data.get("conflicts", [])),
                )
            },
            "consensus_score": data.get(
                "consensus_score",
                data.get("confidence", data.get("total_score", 1.0)),
            ),
        }
        try:
            consensus = float(pseudo_state["consensus_score"])
        except (TypeError, ValueError):
            consensus = 1.0
        pseudo_state["consensus_score"] = consensus

        result = gate.check(pseudo_state)  # type: ignore[arg-type]
        reasons: List[str] = []
        if result["critical_count"] > result["critical_limit"]:
            reasons.append(
                f"{result['critical_count']} critical risks exceed limit {result['critical_limit']}"
            )
        if result["unresolved_conflicts"] > gate.max_unresolved_conflicts:
            reasons.append(
                f"{result['unresolved_conflicts']} unresolved conflicts exceed limit {gate.max_unresolved_conflicts}"
            )
        if result["consensus"] < max(gate.min_consensus, threshold):
            reasons.append(
                f"consensus {result['consensus']:.2f} below {max(gate.min_consensus, threshold):.2f}"
            )
        result["blocked"] = not result["passed"]
        result["reasons"] = reasons
        if not result["passed"]:
            logger.warning("quality_gate_blocked", stage=stage.value, reasons=reasons)
        return result
