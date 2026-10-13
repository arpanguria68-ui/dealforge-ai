import pytest

from app.main import declines_clarification


@pytest.mark.parametrize("prompt", [
    "Evaluate SPT. Proceed with public data only; do not ask clarifying questions.",
    "Don't ask me any clarifying questions, just plan it",
    "no clarifying questions please",
    "Skip the questions and run it",
    "Analyze it without asking clarifying questions",
    "Proceed directly to planning",
])
def test_opt_out_phrases_skip_clarification(prompt):
    assert declines_clarification(prompt)


@pytest.mark.parametrize("prompt", [
    "Evaluate a take-private of Sprout Social (NASDAQ: SPT)",
    "What questions should we ask management about churn?",
    "",
])
def test_ordinary_prompts_still_get_clarification(prompt):
    assert not declines_clarification(prompt)
