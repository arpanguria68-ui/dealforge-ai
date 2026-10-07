"""Simple token counting utility for budget enforcement."""
import tiktoken

class TokenCounter:
    """Estimates token counts for various models."""
    def __init__(self, default_model: str = "gpt-4o"):
        try:
            self.encoding = tiktoken.encoding_for_model(default_model)
        except Exception:
            self.encoding = tiktoken.get_encoding("cl100k_base")

    def count(self, text: str) -> int:
        """Exact count using tiktoken."""
        if not text:
            return 0
        return len(self.encoding.encode(text))

    def estimate(self, text: str) -> int:
        """Alias for count, providing estimating capability."""
        return self.count(text)
