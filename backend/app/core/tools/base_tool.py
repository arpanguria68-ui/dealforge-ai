"""Base classes for all DealForge tools."""
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from abc import ABC, abstractmethod

@dataclass
class ToolResult:
    """Result of a tool execution"""
    success: bool
    data: Any
    error: Optional[str] = None
    execution_time_ms: Optional[float] = None
    provenance_id: Optional[str] = None

# How a tool's output was produced. Anything other than "data" is surfaced to
# the model (description prefix) and stamped on the result so agents and
# reports don't present it as sourced analysis.
OUTPUT_QUALITY_NOTES = {
    "heuristic": "Heuristic keyword/rule screen over the supplied text, not a sourced assessment. "
                 "Treat results as leads to verify; cite as [ESTIMATED].",
    "synthetic_model": "Model trained on synthetic data, not on observed filings. "
                       "Treat scores as indicative only; cite as [ESTIMATED].",
    "template": "Generic template/calculation from the supplied inputs, not deal-specific analysis.",
}


class BaseTool(ABC):
    """Base class for all tools"""

    # "data" (sourced or deterministic on real inputs) | "heuristic" |
    # "synthetic_model" | "template"; see OUTPUT_QUALITY_NOTES.
    output_quality: str = "data"

    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description

    @abstractmethod
    async def execute(self, **kwargs) -> ToolResult:
        pass

    def get_schema(self) -> Dict[str, Any]:
        description = self.description
        if self.output_quality in OUTPUT_QUALITY_NOTES:
            description = f"[{self.output_quality.upper()}] {description}"
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": description,
                "parameters": self.get_parameters_schema(),
            },
        }

    @abstractmethod
    def get_parameters_schema(self) -> Dict[str, Any]:
        pass
