"""DealForge-specific Laya question presets.

Each preset is a ``{question_name: question_spec}`` dict in Laya's wire
format (identical to the Jev /v1/systemone schema):

- ``choice``: pick one named option from ``criteria`` {label: description}
- ``score``: rate on ordered ``criteria`` levels, returns float
- ``noul``: yes/no probability, returns P(yes) in [0, 1]

Keep option labels short: they share the checkpoint's option budget
(head_max_len 192-256 tokens). ~20 options with short descriptions max.
"""

# ── Deal intake triage: which diligence track, how urgent, how complex ──
DEAL_TRIAGE_QUESTIONS = {
    "track": {
        "type": "choice",
        "instructions": "Which diligence track best fits this deal request?",
        "criteria": {
            "financial": "valuation, DCF, comps, LBO, revenue, EBITDA, cash flows",
            "legal": "contracts, compliance, litigation, regulatory, IP ownership",
            "risk": "threats, red flags, downside, fraud, concentration, debt",
            "market": "market size, competition, growth, customers, positioning",
            "tech": "stack, architecture, security, codebase, AI defensibility",
            "esg": "carbon, supply chain, governance, sustainability",
            "general": "everything else or multi-track overview",
        },
    },
    "urgency": {
        "type": "score",
        "instructions": "How urgent is this deal request?",
        "criteria": ["not urgent", "soon", "blocking"],
    },
    "needs_deep_dive": {
        "type": "noul",
        "instructions": "Does this request need multi-agent deep analysis rather than a quick summary?",
    },
}

# ── Confidence pre-gate: fast System-1 check before expensive peer review ──
CONFIDENCE_QUESTIONS = {
    "well_supported": {
        "type": "noul",
        "instructions": "Are the material factual and numerical claims traceable to the supplied fact ledger or cited sources, with derived figures following from those inputs? Treat unsupported precision, invented sources, and mismatched periods as not well-supported.",
    },
    "has_red_flags": {
        "type": "noul",
        "instructions": "Does this analysis contain unsupported factual or numerical claims, arithmetic inconsistencies, conflicts with supplied inputs, mismatched periods or units, or invented source attribution that needs human review?",
    },
    "quality": {
        "type": "score",
        "instructions": "Rate the overall quality of this analysis.",
        "criteria": ["weak", "acceptable", "strong"],
    },
}

# ── RAG relevance: single reusable question, state = "Q: ...\nChunk: ..." ──
RAG_RELEVANCE_QUESTION = {
    "relevant": {
        "type": "noul",
        "instructions": "Is this document chunk directly relevant to answering the query?",
    },
    "quality": {
        "type": "score",
        "instructions": "How useful is this chunk for answering the query?",
        "criteria": ["irrelevant", "background", "directly useful"],
    },
}

# ── Tool pre-routing: narrow 30+ tools to a family in one forward pass ──
TOOL_ROUTING_CRITERIA = {
    "financial": "DCF, multiples, ratios, NPV, IRR, comps, football field, sensitivity, Monte Carlo",
    "search": "web search, SEC filings, company lookup, market data, news",
    "document": "search indexed deal documents, VDR contents, uploaded files",
    "legal_risk": "contract clauses, cyber vulns, antitrust HHI, privacy audit",
    "tech_esg": "AI stack scan, defensibility, carbon, supply chain, ESG score",
    "reporting": "IC memo, deal deck, meeting memo, Excel export",
    "integration": "roadmap, churn model, synergy tracker",
    "none": "no tool needed, answer from context",
}

# ── Model-tier routing: simple task stays local, complex escalates ──
COMPLEXITY_QUESTIONS = {
    "tier": {
        "type": "choice",
        "instructions": "Which model tier does this task need?",
        "criteria": {
            "local": "extraction, formatting, summarization, classification, simple lookup",
            "cloud": "multi-step reasoning, valuation judgment, legal interpretation, debate, synthesis",
        },
    },
    "complexity": {
        "type": "score",
        "instructions": "How complex is this task?",
        "criteria": ["simple", "moderate", "complex"],
    },
}

# ── Debate judging: do two agent conclusions agree? ──
DEBATE_AGREEMENT_QUESTION = {
    "agree": {
        "type": "noul",
        "instructions": "Do these two agent conclusions agree on the core recommendation?",
    },
    "severity": {
        "type": "score",
        "instructions": "How severe is the disagreement between the two positions?",
        "criteria": ["minor", "significant", "fundamental"],
    },
}
