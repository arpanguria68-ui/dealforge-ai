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

class BaseTool(ABC):
    """Base class for all tools"""

    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description

    @abstractmethod
    async def execute(self, **kwargs) -> ToolResult:
        pass

    def get_schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.get_parameters_schema(),
            },
        }

    @abstractmethod
    def get_parameters_schema(self) -> Dict[str, Any]:
        pass
