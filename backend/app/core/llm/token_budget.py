"""Per-source token budgeting for RAG results."""
import structlog
from typing import List, Dict, Any
from app.core.llm.token_counter import TokenCounter

logger = structlog.get_logger()

class TokenBudget:
    """
    Manages a shrinking token budget across multiple sources.
    Ensures the most relevant snippets are kept while staying within limits.
    """
    def __init__(self, total_budget: int = 15000):
        self.total_budget = total_budget
        self.used = 0
        self.counter = TokenCounter()
        self.results = []

    def add_source(self, source_name: str, content: str, metadata: Dict[str, Any] = None) -> bool:
        """
        Adds content if it fits within the remaining budget.
        Returns True if added, False if skipped/truncated.
        """
        if self.used >= self.total_budget:
            return False

        remaining = self.total_budget - self.used
        tokens = self.counter.estimate(content)

        if tokens <= remaining:
            self.results.append({
                "source": source_name,
                "content": content,
                "tokens": tokens,
                "metadata": metadata or {}
            })
            self.used += tokens
            return True
        else:
            # Truncate to fit remaining
            ratio = remaining / tokens
            truncated_len = int(len(content) * ratio * 0.9) # 10% safety margin for chars vs tokens
            truncated_content = content[:truncated_len] + "... [TRUNCATED]"
            
            actual_tokens = self.counter.estimate(truncated_content)
            self.results.append({
                "source": source_name,
                "content": truncated_content,
                "tokens": actual_tokens,
                "metadata": metadata or {}
            })
            self.used += actual_tokens
            return False

    def get_context_block(self) -> str:
        """Formats the collected results into a single context string."""
        blocks = []
        for res in self.results:
            blocks.append(f"--- SOURCE: {res['source']} ---\n{res['content']}\n")
        return "\n".join(blocks)

    def get_stats(self) -> Dict[str, Any]:
        return {
            "total_budget": self.total_budget,
            "used": self.used,
            "remaining": self.total_budget - self.used,
            "source_count": len(self.results)
        }
