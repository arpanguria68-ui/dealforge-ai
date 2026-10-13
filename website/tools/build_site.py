"""Generate the DealForge AI project site (static HTML) into website/.

Usage:  python website/tools/build_site.py
Inputs: website/tools/facts.json (agents + tools extracted from the code registry).
The site has no external requests: fonts, scripts and media are bundled.
"""
import html
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                      # website/
REPO = 'https://github.com/arpanguria68-ui/dealforge-ai'
FACTS = json.load(open(os.path.join(HERE, 'facts.json'), encoding='utf-8'))
AGENTS, TOOLS = FACTS['agents'], FACTS['tools']

GROUPS = [
    ('plan', 'Planning &amp; delivery', ['project_manager', 'ofas_supervisor', 'report_architect', 'compiler_agent']),
    ('data', 'Data &amp; ingestion', ['data_curator', 'prospectus_agent']),
    ('fin', 'Financial analysis &amp; modelling', ['financial_analyst', 'fpa_forecasting_agent', 'valuation_agent',
                                                 'dcf_lbo_architect', 'advanced_financial_modeler', 'treasury_agent',
                                                 'tax_compliance_agent']),
    ('mkt', 'Market &amp; commercial diligence', ['market_researcher', 'due_diligence_agent', 'business_analyst',
                                                 'ai_tech_diligence_agent', 'esg_agent', 'integration_planner_agent']),
    ('risk', 'Risk, legal &amp; compliance', ['risk_assessor', 'market_risk_agent', 'legal_advisor', 'compliance_agent',
                                             'compliance_qa_agent', 'red_team']),
    ('reason', 'Reasoning, scoring &amp; memo', ['complex_reasoning', 'debate_moderator', 'scoring_agent',
                                                'investment_memo_agent']),
]
TOOL_GROUPS = [
    ('Market &amp; financial data', ['fetch_financial_statements', 'sec_filings', 'finnhub_data', 'alpha_vantage',
                                   'financial_datasets', 'company_data', 'market_data', 'peer_discovery',
                                   'fetch_comparable_companies', 'startup_intelligence']),
    ('Modelling &amp; analytics', ['financial_calculator', 'finance_analysis', 'run_sensitivity_analysis',
                                 'run_monte_carlo_irr', 'generate_football_field', 'excel_model_populate',
                                 'excel_export_tables', 'churn_monte_carlo', 'synergy_tracker', 'roadmap_generator',
                                 'antitrust_hhi_calculator']),
    ('Diligence scanners', ['filing_due_diligence', 'legal_clause_analyzer', 'ai_stack_scanner',
                            'model_defensibility_scorer', 'ai_value_quantifier', 'carbon_footprint_extractor',
                            'supply_chain_risk_flagger', 'esg_scorer', 'cyber_vuln_scanner', 'privacy_auditor']),
    ('Research &amp; documents', ['web_search', 'web_scraper', 'document_search']),
    ('Outputs', ['build_document', 'generate_report', 'generate_ic_memo', 'generate_deal_deck', 'generate_meeting_memo']),
]
NAV = [('index.html', 'Overview'), ('docs/product-tour.html', 'Product tour'), ('docs/architecture.html', 'Architecture'),
       ('docs/agents.html', 'Agents'), ('docs/tools-and-data.html', 'Tools &amp; data'),
       ('docs/quality-and-controls.html', 'Quality &amp; controls'), ('docs/reports.html', 'Reports'),
       ('docs/api.html', 'API'), ('docs/getting-started.html', 'Get started')]
SIDE = [('Start here', [('docs/getting-started.html', 'Getting started'), ('docs/product-tour.html', 'Product tour')]),
        ('How it works', [('docs/architecture.html', 'Architecture'), ('docs/agents.html', 'Agent catalog'),
                          ('docs/tools-and-data.html', 'Tools &amp; data sources')]),
        ('Trust', [('docs/quality-and-controls.html', 'Quality &amp; controls'), ('docs/reports.html', 'Reports &amp; review gate')]),
        ('Reference', [('docs/api.html', 'API reference'), ('docs/status.html', 'Status &amp; limits')])]


# ───────────────────────────── helpers ─────────────────────────────
def title_case(key):
    return key.replace('_', ' ').title().replace('Dcf Lbo', 'DCF/LBO').replace('Fpa', 'FP&amp;A').replace('Esg', 'ESG')\
        .replace('Ofas', 'OFAS').replace('Ai ', 'AI ').replace('Pm ', 'PM ')


def e(s):
    return html.escape(s, quote=False)


def code(s):
    return f'<pre><code>{e(s.strip(chr(10)))}</code></pre>'


def table(headers, rows, cls=''):
    th = ''.join(f'<th>{h}</th>' for h in headers)
    body = ''.join('<tr>' + ''.join(f'<td>{c}</td>' for c in r) + '</tr>' for r in rows)
    return f'<div class="tw"><table class="{cls}"><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>'


def note(kind, text):
    cls = {'warn': 'note warn', 'ok': 'note ok'}.get(kind, 'note')
    return f'<div class="{cls}"><p>{text}</p></div>'


def fig(prefix, img, caption, alt=None):
    src = f'{prefix}assets/img/{img}.webp'
    return (f'<figure><img src="{src}" data-zoom="{src}" alt="{e(alt or caption)}" loading="lazy">'
            f'<figcaption>{caption}</figcaption></figure>')


def flow(items):
    out = ''
    for i, (t, d) in enumerate(items, 1):
        out += f'<div class="n"><b class="i">{i}</b><div><strong>{t}</strong><p>{d}</p></div></div>'
    return f'<div class="flow">{out}</div>'


def shell(path, title, body, desc, docs=True):
    prefix = '../' * path.count('/')
    nav = ''.join(f'<a href="{prefix}{href}"{" class=on" if href == path or (docs and path.startswith("docs/") and href == path) else ""}>{label}</a>'
                  for href, label in NAV)
    side = ''
    if docs:
        for head, links in SIDE:
            side += f'<h4>{head}</h4>' + ''.join(
                f'<a href="{prefix}{h}"{" class=on" if h == path else ""}>{l}</a>' for h, l in links)
        content = f'<div class="wrap docs"><aside class="side" aria-label="Documentation">{side}</aside><article class="doc">{body}</article></div>'
    else:
        content = body
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.unescape(title)} · DealForge AI</title>
<meta name="description" content="{e(desc)}">
<meta property="og:title" content="{e(html.unescape(title))} · DealForge AI">
<meta property="og:description" content="{e(desc)}">
<meta property="og:image" content="{prefix}assets/img/poster-full.jpg">
<link rel="stylesheet" href="{prefix}assets/css/site.css">
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<header class="bar"><div class="wrap">
<a class="brand" href="{prefix}index.html"><span class="mark">DF</span>DealForge AI</a>
<nav aria-label="Main">{nav}</nav>
<a class="gh" href="{REPO}">GitHub</a>
</div></header>
<main id="main">{content}</main>
<footer><div class="wrap"><span>DealForge AI · multi-agent M&amp;A deal analysis · all figures in the demos come from public filings (Sprout Social, NASDAQ: SPT).</span>
<span><a href="{REPO}">Source</a> · <a href="{prefix}docs/status.html">Status &amp; limits</a> · <a href="{prefix}docs/getting-started.html">Get started</a></span></div></footer>
<script src="{prefix}assets/js/site.js"></script>
</body>
</html>
"""


def write(path, text):
    text = text.replace('{{', '{').replace('}}', '}')
    full = os.path.join(ROOT, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(text)


# ───────────────────────────── diagrams ─────────────────────────────
def architecture_svg():
    def box(x, y, w, h, t, sub='', fill='#e6f0fb', stroke='#0f6cbd'):
        s = (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{fill}" stroke="{stroke}" stroke-width="1.4"/>'
             f'<text x="{x + w / 2}" y="{y + 26}" text-anchor="middle" font-weight="700" font-size="15" fill="#0b2545">{t}</text>')
        if sub:
            for k, line in enumerate(sub.split('|')):
                s += f'<text x="{x + w / 2}" y="{y + 47 + k * 17}" text-anchor="middle" font-size="12.5" fill="#4a5d78">{line}</text>'
        return s

    def arrow(x1, y1, x2, y2):
        return (f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#0f6cbd" stroke-width="1.8" marker-end="url(#a)"/>')

    s = ['<svg class="diagram" viewBox="0 0 1100 640" role="img" aria-label="DealForge AI architecture">',
         '<defs><marker id="a" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto"><path d="M0,0 L9,4.5 L0,9 z" fill="#0f6cbd"/></marker></defs>',
         '<text x="20" y="30" font-size="13" font-weight="700" fill="#4a5d78">CLIENT</text>',
         box(30, 44, 1040, 74, 'React 19 + TypeScript UI (Vite)', 'Chat · Dashboard · Task Board · Knowledge Base · Settings   |   Zustand store · Radix/shadcn · Tailwind · Recharts'),
         arrow(550, 118, 550, 156),
         '<text x="20" y="150" font-size="13" font-weight="700" fill="#4a5d78">API</text>',
         box(30, 160, 1040, 74, 'FastAPI backend (~100 routes)', '/chat/clarify · /chat/plan · /chat/execute-task · /tasks · /deals/{id}/documents · /dashboard/metrics · /ofas · /workflows · /mcp · SSE stream'),
         arrow(300, 234, 300, 276), arrow(800, 234, 800, 276),
         '<text x="20" y="268" font-size="13" font-weight="700" fill="#4a5d78">ORCHESTRATION</text>',
         box(30, 280, 500, 120, 'Planner &amp; task engine', 'Scrum Master (ProjectManager) → clarify → plan|TaskManager: dependency-ordered tasks, approve/edit/reorder|Execute-task: one agent per task, upstream results as context'),
         box(570, 280, 500, 120, 'Agent crew (29 registered agents)', 'BaseAgent: generate_with_tools · per-agent tool allow-lists|Native function calls or a text tool protocol|Evidence-aware prompts, validation, tool-outcome recording'),
         arrow(300, 400, 300, 442), arrow(820, 400, 820, 442), arrow(550, 400, 420, 442),
         '<text x="20" y="434" font-size="13" font-weight="700" fill="#4a5d78">CAPABILITIES</text>',
         box(30, 446, 330, 118, 'LLM layer', 'ModelRouter (local-first, fallback)|LLM Gateway: rate limits, retry, budget|Gemini · Claude · OpenAI · Mistral · Groq|OpenRouter · NVIDIA · Vertex · Ollama · LM Studio|any OpenAI-compatible endpoint', fill='#eaf7ef', stroke='#1b9b57'),
         box(385, 446, 330, 118, 'Tools (39)', 'SEC EDGAR · Finnhub · Alpha Vantage|financial_datasets · web search/scraper|DCF/LBO/sensitivity/Monte Carlo engines|diligence scanners · document builders|MCP (read-only external servers)', fill='#eaf7ef', stroke='#1b9b57'),
         box(740, 446, 330, 118, 'Memory &amp; state', 'Redis: deals, conversations, activity, artifacts|SQLite: tasks (SQLAlchemy async)|PageIndex (tree RAG) + hybrid search|Knowledge graph (SQLite / Neo4j)|Provenance audit trail', fill='#eaf7ef', stroke='#1b9b57'),
         arrow(550, 564, 550, 590),
         '<rect x="30" y="592" width="1040" height="40" rx="12" fill="#fff0d1" stroke="#e08a00" stroke-width="1.4"/>',
         '<text x="550" y="617" text-anchor="middle" font-size="13" fill="#4a5d78"><tspan font-weight="700" fill="#0b2545">Quality layer → documents: </tspan>evidence brief · memo grounding · cross-agent reconciliation · review gate → Word · PowerPoint · Excel · PDF</text>',
         '</svg>']
    return ''.join(s)


def flow_svg():
    steps = [('Describe', 'Chat prompt'), ('Clarify', '≤3 rounds, skippable'), ('Plan', 'Scrum Master'), ('Approve', 'Human gate'),
             ('Execute', 'Agents + tools'), ('Verify', 'Ground + reconcile'), ('Deliver', 'Brief + 4 formats')]
    w, gap, x0 = 128, 22, 20
    parts = ['<svg class="diagram" viewBox="0 0 1100 130" role="img" aria-label="Pipeline">',
             '<defs><marker id="b" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="#0f6cbd"/></marker></defs>']
    for i, (a, b) in enumerate(steps):
        x = x0 + i * (w + gap)
        fill = '#fff0d1' if a == 'Approve' else '#e6f0fb'
        stroke = '#e08a00' if a == 'Approve' else '#0f6cbd'
        parts.append(f'<rect x="{x}" y="28" width="{w}" height="70" rx="12" fill="{fill}" stroke="{stroke}" stroke-width="1.4"/>')
        parts.append(f'<text x="{x + w / 2}" y="58" text-anchor="middle" font-weight="700" font-size="16" fill="#0b2545">{a}</text>')
        parts.append(f'<text x="{x + w / 2}" y="80" text-anchor="middle" font-size="11.5" fill="#4a5d78">{b}</text>')
        if i < len(steps) - 1:
            parts.append(f'<line x1="{x + w}" y1="63" x2="{x + w + gap - 2}" y2="63" stroke="#0f6cbd" stroke-width="1.8" marker-end="url(#b)"/>')
    parts.append('</svg>')
    return ''.join(parts)


# ───────────────────────────── pages ─────────────────────────────
def page_index():
    prefix = ''
    features = [
        ('🧭', 'Plan-first, human-approved', 'A Scrum Master agent turns a one-line brief into a dependency-ordered task plan. <b>Nothing runs until you approve it</b>; you can edit, add, delete and reorder tasks first.'),
        ('🤝', '29 specialist agents', 'Financial analysis, valuation, DCF/LBO, legal, risk, red-team, ESG, tax, treasury, integration and more, each with its own tool allow-list.'),
        ('🔌', '39 tools &amp; live data', 'SEC EDGAR filings, Finnhub, Alpha Vantage, financial_datasets, web search, a DCF/LBO/sensitivity/Monte Carlo toolbox and document builders.'),
        ('🧮', 'Shared, sourced valuation inputs', 'A valuation agent builds WACC, growth and FCF from filings and market data; DCF/LBO agents start from the same assumptions.'),
        ('🛡️', 'It checks its own work', 'Memo figures are traced to recorded evidence, DCFs are compared across agents, broken DCF bridges are recomputed, and unusable model replies are rejected.'),
        ('📄', 'Word, PowerPoint, Excel, PDF', 'Edited reports (not agent dumps) with a football field, evidence tables, source register and a review gate: drafts stay drafts until a reviewer approves.'),
        ('📊', 'Pipeline dashboard', 'Valuation vs market EV, sourcing and tool-call counters, review flags and the exact reconciliation warnings, per deal.'),
        ('🗂️', 'Knowledge base', 'Local tree-index RAG (no vector DB needed), hybrid BM25 + dense + tree search, and a deal knowledge graph that feeds the risk register.'),
        ('🔁', 'Any model, your keys', 'Local-first routing with cloud fallback, Gemini, Claude, OpenAI, Mistral, Groq, OpenRouter, NVIDIA, Vertex, Ollama, LM Studio or any OpenAI-compatible endpoint.'),
    ]
    cards = ''.join(f'<div class="card"><div class="ico">{i}</div><h3>{t}</h3><p>{d}</p></div>' for i, t, d in features)
    steps = [('Describe the deal', 'Type a deal in chat. Optional clarifying questions (max three rounds) can be skipped.'),
             ('Review the plan', 'See every task, its agent and priority. Edit, add or reorder.'),
             ('Approve &amp; run', 'Agents execute in dependency order and call real tools; progress streams into the chat.'),
             ('Verify', 'Figures are matched to evidence, models are reconciled, and disagreements are surfaced.'),
             ('Read the brief', 'An executive brief with evidence snapshot, findings, risks and clickable sources.'),
             ('Release', 'Generate Word, PowerPoint, Excel and PDF; a reviewer approves them for release.')]
    st = ''.join(f'<div class="step"><h3>{t}</h3><p>{d}</p></div>' for t, d in steps)
    shots = [('chat-approval', 'The approval gate: nothing runs until you approve'), ('chat-done-brief', 'Executive brief with evidence snapshot'),
             ('dashboard-pipeline', 'Pipeline row: valuation, sourcing, review flags'), ('taskboard-plan', 'Task board: edit, add, reorder, approve'),
             ('report-deck-football', 'Football field with market EV and LBO entry'), ('report-word-summary', 'Word report key messages')]
    shot_html = ''.join(f'<button class="shot" data-zoom="assets/img/{n}.webp"><img src="assets/img/{n}.webp" alt="{e(c)}" loading="lazy"><span>{c}</span></button>' for n, c in shots)
    shorts = [('short-1', 'Describe the deal'), ('short-2', 'Meet the crew'), ('short-3', 'Pipeline at a glance'),
              ('short-4', 'Four documents'), ('short-5', 'Your plan. Your call.')]
    vids = ''.join(f'<figure><video controls preload="none" poster="assets/img/poster-{k}.jpg"><source src="assets/video/{k}.mp4" type="video/mp4"></video><figcaption>{c}</figcaption></figure>' for k, c in shorts)
    stack = [('Frontend', 'React 19, TypeScript, Vite, Tailwind, Radix/shadcn UI, Zustand, Recharts, react-markdown'),
             ('Backend', 'Python 3.11, FastAPI, Pydantic, SQLAlchemy (async) + SQLite, Redis, structlog, httpx'),
             ('Agents &amp; LLMs', 'Custom BaseAgent with tool loop, ModelRouter + LLM Gateway, 10+ provider clients, OpenAI-compatible endpoint support'),
             ('Retrieval', 'PageIndex local tree RAG, BM25 + dense + tree reciprocal-rank fusion, Neo4j-optional knowledge graph, MCP client'),
             ('Documents', 'python-docx, python-pptx, openpyxl, ReportLab / fpdf2, Plotly (kaleido) with a Matplotlib fallback'),
             ('Quality', '448 automated tests, deterministic validators, provenance audit trail, Docker Compose deployment')]
    stk = ''.join(f'<div class="card"><h3>{a}</h3><p>{b}</p></div>' for a, b in stack)
    body = f"""
<div class="hero"><div class="wrap grid">
<div>
<span class="eyebrow">Open-source · multi-agent M&amp;A deal analysis</span>
<h1>Describe a deal.<br>A crew of agents does the diligence. <em>You stay in control.</em></h1>
<p class="lead">DealForge AI plans the analysis, waits for your approval, runs specialist agents against real filings and market data, checks its own numbers, and hands back drafts a human has to sign.</p>
<div class="cta"><a class="btn p" href="#demo">▶ Watch it work</a><a class="btn s" href="docs/getting-started.html">Get started</a><a class="btn s" href="docs/architecture.html">Architecture</a></div>
<div class="facts"><span><b>29</b>agents</span><span><b>39</b>tools</span><span><b>4</b>report formats</span><span><b>448</b>tests</span></div>
</div>
<div class="frame"><div class="chrome"><i></i><i></i><i></i></div>
<video controls preload="metadata" poster="assets/img/poster-full.jpg"><source src="assets/video/full-film.mp4" type="video/mp4"></video></div>
</div></div>

<section><div class="wrap"><div class="sec-head"><h2>What it does</h2><p>An end-to-end deal workspace: from a one-line brief to a reviewed deliverable, with the reasoning and sources kept visible.</p></div>
<div class="cards">{cards}</div></div></section>

<section class="alt"><div class="wrap"><div class="sec-head"><h2>How it works</h2><p>The chat pipeline is human-in-the-loop by design.</p></div>
{flow_svg()}<div style="height:34px"></div><div class="steps">{st}</div>
<p style="margin-top:22px"><a href="docs/architecture.html">Read the architecture →</a></p></div></section>

<section id="demo"><div class="wrap"><div class="sec-head"><h2>Watch the agents work</h2>
<p>Real captures of a Sprout Social run in the chat UI: prompt, plan, approval, a sped-up recording of the agents executing, and the finished brief.</p></div>
<div class="frame big-video"><div class="chrome"><i></i><i></i><i></i></div>
<video controls preload="none" poster="assets/img/poster-chat.jpg"><source src="assets/video/chat-walkthrough.mp4" type="video/mp4"></video></div>
<div class="videos">{vids}</div></div></section>

<section class="alt"><div class="wrap"><div class="sec-head"><h2>Product tour</h2><p>Real screens from the running app. Click any image to enlarge, or follow the <a href="docs/product-tour.html">guided tour</a>.</p></div>
<div class="shots">{shot_html}</div></div></section>

<section><div class="wrap"><div class="sec-head"><h2>The crew</h2>
<p>29 registered agents across six groups. Each one only sees the tools on its own allow-list, and every tool call is recorded with its outcome.</p></div>
<div class="cards">{''.join(f'<div class="card"><h3>{n}</h3><p>{len(ids)} agents: ' + ', '.join(title_case(i) for i in ids[:4]) + (' …' if len(ids) > 4 else '') + '</p></div>' for _, n, ids in GROUPS)}</div>
<p style="margin-top:22px"><a href="docs/agents.html">Browse the full agent catalog →</a> · <a href="docs/tools-and-data.html">Tools &amp; data sources →</a></p></div></section>

<section class="alt"><div class="wrap"><div class="sec-head"><h2>Trust by design</h2><p>Agents are fallible. DealForge assumes so and shows its work.</p></div>
<div class="cards">
<div class="card"><span class="pill g">Gate</span><h3>Approval before execution</h3><p>The plan is shown first. Execution only starts after an explicit approve.</p></div>
<div class="card"><span class="pill g">Check</span><h3>Evidence-grounded memo</h3><p>Every figure in the memo is matched against recorded agent output; the grounding score is shown (for example 46 of 51 figures).</p></div>
<div class="card"><span class="pill a">Flag</span><h3>Cross-agent reconciliation</h3><p>DCFs more than 15% apart, an LBO entering below market value, or a DCF bridge that does not add up are flagged, not hidden.</p></div>
<div class="card"><span class="pill g">Repair</span><h3>Deterministic fallbacks</h3><p>A missing LBO or a broken DCF bridge is rebuilt from sourced inputs with every assumption listed.</p></div>
<div class="card"><span class="pill a">Gate</span><h3>Reviewer approval for release</h3><p>Reports download as <code>_DRAFT_not_approved</code> until a reviewer approves them.</p></div>
<div class="card"><span class="pill">Audit</span><h3>Provenance trail</h3><p>Tool calls, parameters, responses and timestamps are recorded and exportable per deal.</p></div>
</div><p style="margin-top:22px"><a href="docs/quality-and-controls.html">How the controls work →</a></p></div></section>

<section><div class="wrap"><div class="sec-head"><h2>Tech stack</h2></div><div class="cards">{stk}</div></div></section>

<section class="alt"><div class="wrap"><div class="sec-head"><h2>Run it yourself</h2><p>Docker Compose brings up the API, the UI and Redis.</p></div>
{code('''git clone https://github.com/arpanguria68-ui/dealforge-ai.git
cd dealforge-ai
cp .env.docker.example .env.docker   # add at least one LLM provider key
docker compose --env-file .env.docker up --build -d
# UI  http://localhost:3000     API  http://localhost:8005/docs''')}
<p>No Docker? See the <a href="docs/getting-started.html">local setup</a>. The honest list of what is solid and what is experimental is on the <a href="docs/status.html">status page</a>.</p></div></section>
"""
    write('index.html', shell('index.html', 'Multi-agent M&A deal analysis', body,
                              'DealForge AI: a multi-agent M&A deal analysis workspace. Plan, approve, run specialist agents on real filings, verify, and release reviewed Word, PowerPoint, Excel and PDF reports.', docs=False))


def page_getting_started():
    body = f"""
<h1>Getting started</h1>
<p>DealForge AI is a FastAPI backend plus a React UI. It needs Redis for deals, conversations and generated artifacts, and at least one LLM provider.</p>
<h2>Requirements</h2>
{table(['Component', 'Version', 'Notes'], [
        ['Python', '3.11+', 'Backend (the Dockerfile uses <code>python:3.11-slim</code>)'],
        ['Node.js', '20+', 'Frontend build and dev server'],
        ['Redis', '7', 'Required. Docker Compose starts one; locally run <code>redis-server</code>'],
        ['LLM access', 'any one', 'A cloud key (Gemini, OpenAI, Mistral, …), a local model (Ollama / LM Studio) or any OpenAI-compatible endpoint'],
        ['Optional data keys', '', 'Finnhub, Alpha Vantage, financial_datasets, FMP. SEC EDGAR needs no key'],
    ])}
<h2>Option A · Docker Compose</h2>
{code('''git clone https://github.com/arpanguria68-ui/dealforge-ai.git
cd dealforge-ai
# create .env.docker with your keys (see the table below), then:
docker compose --env-file .env.docker up --build -d''')}
<p>The UI is served on <code>http://localhost:3000</code> and the API on <code>http://localhost:8005</code> (interactive docs at <code>/docs</code>). Generated reports persist in the <code>dealforge-reports</code> volume.</p>
<h2>Option B · Local development</h2>
{code('''# 1. Redis
redis-server

# 2. Backend (from the repository root)
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8005

# 3. Frontend (second terminal)
cd frontend
npm install
npm run dev''')}
<p>The frontend talks to <code>&lt;current-host&gt;:8005</code> by default; override with <code>VITE_API_BASE</code>. The backend allows the origins <code>localhost:3000, 3001, 5173, 5174</code> out of the box; set <code>CORS_ORIGINS</code> for anything else.</p>
<h2>Configure a model</h2>
<p>Open <strong>Settings</strong> in the UI and add a provider key, or set the environment variables below. To use any OpenAI-compatible server (a local gateway, vLLM, a proxy), choose <strong>Custom endpoint</strong> and give it a base URL, model name and key. Per-agent routing is stored in <code>settings.json</code> and can pin individual agents to individual providers.</p>
{note('', 'Keys are masked in the UI and are never sent to the browser in full. Do not commit <code>settings.json</code> or <code>.env</code> files: the repository ignores them.')}
<h2>Environment variables</h2>
{table(['Variable', 'Purpose'], [
        ['<code>GEMINI_API_KEY</code>, <code>OPENAI_API_KEY</code>, <code>MISTRAL_API_KEY</code>, <code>OPENROUTER_API_KEY</code>, <code>NVIDIA_API_KEY</code>', 'Cloud LLM providers'],
        ['<code>DEFAULT_LLM_PROVIDER</code>', 'Fallback provider (default <code>gemini</code> in Compose)'],
        ['<code>FINNHUB_API_KEY</code>, <code>ALPHA_VANTAGE_API_KEY</code>, <code>FINANCIAL_DATASETS_API_KEY</code>, <code>FMP_API_KEY</code>, <code>SEC_API_KEY</code>', 'Market and filing data (all optional; SEC EDGAR XBRL needs none)'],
        ['<code>REDIS_URL</code>', 'Default <code>redis://localhost:6379/0</code>'],
        ['<code>DATABASE_URL</code>', 'Default SQLite via aiosqlite'],
        ['<code>PAGEINDEX_MODE</code>', '<code>local</code> (self-hosted tree RAG, default) or <code>cloud</code>'],
        ['<code>KG_BACKEND</code>', 'Knowledge graph: <code>sqlite</code> (default), <code>neo4j</code>, or <code>off</code>'],
        ['<code>AGENT_TIMEOUT_SECONDS</code>', 'Per-agent wall-clock limit (default 180)'],
        ['<code>CUSTOM_ENDPOINT_TIMEOUT_SECONDS</code>', 'Timeout for the custom endpoint client (default 155)'],
        ['<code>ADMIN_API_TOKEN</code>, <code>REQUIRE_ADMIN_TOKEN</code>', 'Bearer-token guard for report generation, download and approval endpoints'],
        ['<code>CORS_ORIGINS</code>', 'Comma-separated allowed browser origins'],
    ])}
<h2>Your first analysis</h2>
{flow([('Open the Chat tab and click <em>New Analysis</em>', 'The welcome message explains the flow.'),
         ('Type a deal', 'For example: <code>Evaluate a take-private of Sprout Social (NASDAQ: SPT). Proceed with public data only; do not ask clarifying questions.</code> Saying you do not want questions skips the clarification step.'),
         ('Review the plan', 'The Scrum Master lists the tasks and the agent for each. A valuation agent is added automatically whenever a DCF or LBO is planned.'),
         ('Click Approve &amp; run', 'Agents run in dependency order. Expect several minutes with a remote chat-style model; local models vary.'),
         ('Read the brief, then open the Dashboard', 'Use <em>View Analysis → Generate</em> for the documents; a reviewer approves them for release.')])}
<h2>Tests</h2>
{code('''cd backend
pytest -q''')}
<p>The suite has 448 tests. Two tests fail on Windows for environmental reasons (symlink privilege and hard-coded Windows paths); they are unrelated to behavior.</p>
<h2>Troubleshooting</h2>
{table(['Symptom', 'Likely cause'], [
        ['The UI loads but every call fails', 'Backend not on port 8005, or the UI origin is missing from <code>CORS_ORIGINS</code>'],
        ['Backend exits on start', 'Redis is not running or <code>REDIS_URL</code> is wrong'],
        ['Agents return "tool request" text or empty results', 'The model does not support native tool calls; use a provider that does, or the OpenAI-compatible endpoint (which uses the built-in text tool protocol)'],
        ['Report downloads return 409', 'Reports are not approved yet; use the draft download (<code>?draft=true</code>) or approve them from the Dashboard'],
        ['No peer multiples in comps', 'Finnhub key missing or rate limited; the valuation agent records this under "Not retrieved"'],
    ])}
"""
    write('docs/getting-started.html', shell('docs/getting-started.html', 'Getting started', body, 'Install and run DealForge AI with Docker Compose or locally, configure models and data keys, and run a first analysis.'))


def page_product_tour():
    p = '../'
    body = f"""
<h1>Product tour</h1>
<p>Everything on this page is a real capture from the running application, using public Sprout Social (NASDAQ: SPT) data.</p>
<h2>See a run, end to end</h2>
<div class="frame big-video"><div class="chrome"><i></i><i></i><i></i></div><video controls preload="none" poster="{p}assets/img/poster-chat.jpg"><source src="{p}assets/video/chat-walkthrough.mp4" type="video/mp4"></video></div>
<p>The video combines screenshots of the prompt, plan and approval gate, a screen recording of the agents executing (sped up 10x, since real execution takes minutes), and screenshots of the finished brief.</p>

<h2>1 · Chat: from a brief to an approved plan</h2>
{fig(p, 'chat-welcome', 'The Chat tab. Conversations on the left, the agent thread in the middle, the composer with focus mode (Speed / Balanced / Quality) and Sources at the bottom.')}
{fig(p, 'chat-typed', 'Describe the deal in plain language. If you say you do not want questions, the clarification step is skipped.')}
{fig(p, 'chat-thinking', 'The Scrum Master (ProjectManager agent) reasons about data needs and builds the plan.')}
{fig(p, 'chat-plan', 'The plan: each task shows its title, the specialist agent that owns it and a priority. A valuation agent is added automatically when a DCF or LBO is planned.')}
{fig(p, 'chat-approval', 'The approval gate. The warning bar and the Approve &amp; run button are the only way to start execution. Cancel discards the plan.')}
{note('ok', '<strong>Why this matters:</strong> agents call paid APIs and run for minutes. The plan is shown first so you control scope and cost, and you can also edit the plan on the Task Board before approving.')}

<h2>2 · Agents at work</h2>
<p>After approval, tasks run in dependency order. Data tasks first, then valuation, then the DCF/LBO and risk agents that depend on it, then the memo. Independent tasks run in parallel. Each agent posts a card to the thread when it finishes, with its provider, duration, a note that confidence is <em>not calibrated</em>, and validation status.</p>
{fig(p, 'chat-done-agents', 'Agent cards in the thread: what each agent was asked, its result summary and the validation line (for example "no arithmetic inconsistencies found in the fields checked").')}

<h2>3 · The brief</h2>
<p>When every task has returned, the chat builds an executive brief from recorded outputs only: an evidence snapshot with formatted values and periods, derived findings, key risks, and, when agents disagree, an <strong>Agents Disagree</strong> section with the exact warnings.</p>
{fig(p, 'chat-done-brief', 'The brief. Values are reproduced from agent outputs; missing values are never filled in, and model confidence is labelled as uncalibrated.')}
<p>Below the brief you will see <strong>Report</strong> and <strong>Data</strong> buttons (Word report and structured JSON). Until a reviewer approves, the Word report downloads as <code>…_DRAFT_not_approved.docx</code> and the chat tells you so.</p>

<h2>4 · Dashboard</h2>
{fig(p, 'dashboard-overview', 'Overview: active, completed and needs-review counts, Valuation vs Market EV per deal, agent utilization, deal pipeline and a live agent-activity feed.')}
{fig(p, 'dashboard-pipeline', 'A pipeline row. Chips show the weighted valuation, DCF, comps and market EV, how many figures are sourced (for example 64/77), tool-call success, and a Review required flag. The amber bullets are the actual reconciliation warnings.')}
{fig(p, 'dashboard-activity', 'The agent-activity feed (right) lists each agent completion with its provider.')}
<p><strong>View Analysis</strong> opens the deal: you can preview the document plan, add a free-text request ("focus on LBO returns for the IC"), toggle <em>fill gaps</em> (research missing items), generate the four documents, approve them for release and download drafts or the bundle.</p>

<h2>5 · Task Board</h2>
{fig(p, 'taskboard', 'Every plan the Project Manager has produced, with status and task counts. Create a plan for any deal ID and company name.')}
{fig(p, 'taskboard-plan', 'An expanded plan. Edit and delete icons on each task, drag handles, priority tags, the owning agent, + Add Task, and the green Approve button for draft plans.')}
<p>Plans can be edited (title, description, agent, priority), extended with your own tasks, reordered, approved and executed from here or from the chat. These actions map to the <code>/api/v1/tasks/…</code> endpoints.</p>

<h2>6 · Reports</h2>
<p>From a finished analysis the app generates Word, PowerPoint, Excel and PDF. See <a href="reports.html">Reports &amp; review gate</a> for the full pipeline and gallery.</p>
{fig(p, 'report-deck-football', 'PowerPoint football field with the current market EV and LBO entry as reference lines.')}
"""
    write('docs/product-tour.html', shell('docs/product-tour.html', 'Product tour', body, 'A guided tour of the DealForge AI chat, dashboard, task board and reports with real screenshots and a recorded run.'))


def page_architecture():
    body = f"""
<h1>Architecture</h1>
<p>DealForge is a conventional web app wrapped around an unconventional core: a crew of tool-using agents whose work is planned, gated, verified and edited before a human sees it.</p>
{architecture_svg()}

<h2>The chat pipeline (the path the UI uses)</h2>
{flow([
        ('Clarify <code>POST /api/v1/chat/clarify</code>', 'The ProjectManager agent identifies data needs and asks up to three rounds of questions. The user can skip or answer; skipped questions are self-researched and summarised as assumptions. A prompt that says "do not ask clarifying questions" skips the step. A guard validates prompt length and content first.'),
        ('Plan <code>POST /api/v1/chat/plan</code>', 'The LLM drafts tasks; the planner then enforces structure: stage dependencies (data → analysis → synthesis), a valuation agent whenever a DCF/LBO is planned, an LBO task routed to the DCF/LBO architect when requested, and focus-mode limits. Tasks persist as a todo list with dependencies.'),
        ('Approve (UI)', 'The plan is presented with an Approve &amp; run bar. The list can also be edited via <code>/api/v1/tasks/…</code>.'),
        ('Execute <code>POST /api/v1/chat/execute-task</code>', 'One call per task. The server checks that the list is approved and dependencies are done, loads upstream results as context, routes the agent to a provider, and runs it with its tool allow-list under a wall-clock timeout (default 180 s). Results, tool-use outcomes and activity events are stored.'),
        ('Synthesize', 'The investment-memo agent runs last with every upstream result. Memo grounding and cross-agent reconciliation run here; the chat builds the brief and follow-ups.'),
        ('Deliver', 'Dashboard metrics and <code>/api/v1/deals/{{id}}/documents/…</code> read the same persisted results to generate and release reports.')])}

<h2>Agent runtime</h2>
<p>All agents extend <code>BaseAgent</code> (<code>backend/app/agents/base.py</code>):</p>
<ul>
<li><strong>Tool loop.</strong> <code>generate_with_tools</code> lets the model call tools until it produces an answer. Providers with native function calling use it; others (including the OpenAI-compatible custom endpoint) use a text tool protocol where the model emits a JSON block with <code>command</code> and <code>args</code>. The protocol rejects fabricated sources: an agent may not cite a tool it did not call.</li>
<li><strong>Allow-lists.</strong> <code>AGENT_TOOL_MAP</code> gives each agent only the tools it needs (for example <code>valuation_agent</code> has 15, <code>treasury_agent</code> has 2). Tool-family shortlisting (Laya) narrows the list per task, and retrieval tools are always kept for data-collection tasks.</li>
<li><strong>Recorded outcomes.</strong> Every call is stored with name, success flag and error, and shown in the dashboard ("4 tool calls ok, 2 failed").</li>
<li><strong>Unusable replies.</strong> Empty replies, canned chat-product errors ("Sorry, something went wrong…") and short off-topic chatter are treated as failures and retried once, not saved as analysis.</li>
<li><strong>Structured output.</strong> <code>run_with_structure</code> parses JSON and falls back to narrative with a flag when the model returns prose.</li>
</ul>

<h2>Model layer</h2>
{table(['Component', 'Role'], [
        ['<code>ModelRouter</code>', 'Local-first routing: try a local model (Ollama / LM Studio) where configured, escalate to cloud on failure; a per-agent provider map comes from settings. The provider actually used is logged and shown in the UI.'],
        ['<code>LLM Gateway</code>', 'Central rolling-window rate limits per vendor, exponential backoff with jitter, cloud→cloud→local fallback, daily budget guards and deterministic-call caching.'],
        ['Provider clients', 'Gemini, Vertex, Claude, OpenAI, Mistral, Groq, OpenRouter, NVIDIA, local (Ollama / LM Studio) and a generic OpenAI-compatible <em>custom endpoint</em>.'],
        ['Capability probe', 'Detects what a model/provider supports (for example native tool calls) so the right protocol is used.'],
        ['Context guard &amp; token budget', 'Keeps prompts within the model window and tracks usage (<code>GET /api/v1/llm/usage</code>).'],
    ])}

<h2>Memory and state</h2>
{table(['Store', 'What lives there'], [
        ['Redis', 'Deals, conversations, the global activity feed, and generated document artifacts with their release metadata'],
        ['SQLite (SQLAlchemy async)', 'Task lists and task items: the persisted plan and each agent\'s result'],
        ['PageIndex (local)', 'Hierarchical tree indexes of uploaded documents; vectorless, reasoning-based retrieval'],
        ['Hybrid search', 'Reciprocal-rank fusion of tree score, BM25 and dense similarity, with an optional Laya re-rank. Fail-soft'],
        ['Knowledge graph', 'Deal facts, entities and risks written by agents; SQLite by default, Neo4j optional (<code>KG_BACKEND</code>). Feeds the cross-agent risk register'],
        ['Provenance', 'Audit trail of tool calls with parameters, raw response, timestamp and freshness; exportable per deal'],
    ])}

<h2>Orchestration modes</h2>
<p>The same agents can be driven three ways:</p>
{table(['Mode', 'Entry point', 'Status'], [
        ['<strong>Interactive chat pipeline</strong> (plan → approve → execute-task)', '<code>/api/v1/chat/*</code>, <code>/api/v1/tasks/*</code>', '<span class="pill g">Primary path. Exercised end to end and covered by tests</span>'],
        ['<strong>Deal orchestrator</strong> (LangGraph state graph)', '<code>POST /api/v1/deals/{{id}}/run</code>', '<span class="pill a">Implemented, less exercised</span>'],
        ['<strong>OFAS missions</strong> (RACI task graph with review gates and recovery)', '<code>/api/v1/ofas/*</code>', '<span class="pill a">Implemented, less exercised</span>'],
        ['<strong>Specialist workflows</strong> (GP-led continuation vehicle, LMT stress, Monte Carlo)', '<code>/api/v1/workflows/*</code>', '<span class="pill a">Implemented, less exercised</span>'],
    ])}
<p>The deal orchestrator is a LangGraph graph with nodes <code>init → fact_base_ingestion → retrieval → task_generation → screening → parallel_analysis → consistency_check → advanced_financial → data_curator → complex_reasoning → report_architect → debate → red_team → scoring → halugate_verify → stakeholder_simulation → report_formatting → compiler → decision</code> with conditional edges, quality gates, a multi-round agent <strong>debate engine</strong>, peer confidence gates, an inter-agent message bus, a user-input pause gate and a HaluGate NLI hallucination check. These pieces are in the repository and API-exposed; the product documentation and the demos here concentrate on the interactive pipeline because that is the one verified end to end.</p>

<h2>Deployment</h2>
<p>Docker Compose defines <code>backend</code> (FastAPI, port 8005→8000), <code>frontend</code> (nginx, port 3000) and <code>redis</code> (7-alpine) with volumes for data and reports. CORS origins, admin-token enforcement, request-size guards (<code>API_MAX_JSON_MB</code>) and path-traversal guards for uploads are configured in the backend.</p>
"""
    write('docs/architecture.html', shell('docs/architecture.html', 'Architecture', body, 'DealForge AI architecture: chat pipeline, agent runtime, model layer, memory, orchestration modes and deployment.'))


def page_agents():
    p = '../'
    filt = '<div class="filter"><button class="on" data-group="all">All (29)</button>' + ''.join(
        f'<button data-group="{g}">{n} ({len(ids)})</button>' for g, n, ids in GROUPS) + '</div>'
    cards = ''
    for g, n, ids in GROUPS:
        for i in ids:
            a = AGENTS[i]
            tools = ''.join(f'<code>{t}</code>' for t in a['allowed_tools'])
            cards += (f'<div class="agent" data-group="{g}"><h3>{title_case(i)}</h3><div class="id">{i}</div>'
                      f'<p>{e(a["description"])}</p><div class="chips">{tools or "<span class=pill>no tools</span>"}</div></div>')
    body = f"""
<h1>Agent catalog</h1>
<p>29 agents are registered in <code>get_agent_registry()</code>. Each one has a role, a model route (from settings) and an explicit tool allow-list. The table below is generated from the code registry, so it reflects what is actually registered.</p>
{filt}
<div class="agents">{cards}</div>

<h2>How agents are chosen</h2>
<ul>
<li>The <strong>Scrum Master</strong> (<code>project_manager</code>) picks agents per task when it plans. Focused requests may get one task; broad requests (full diligence, IC prep) get a multi-stage plan.</li>
<li>The planner then <strong>enforces structure</strong>: data collection first, modelling agents after the valuation agent so they share its WACC and growth, synthesis agents last, and the memo depends on everything.</li>
<li>An agent running a task receives the upstream results of the tasks it depends on, not the whole conversation.</li>
</ul>
<h2>Examples of agents working together</h2>
{table(['Agent', 'Typical contribution on a take-private'], [
        ['Data Curator', 'Pulls SEC statements and market data; normalizes them into one unit-scaled data pack'],
        ['Valuation Agent', 'Peer multiples (size-filtered), CAPM WACC, FCF from filings, market EV; records its audit trail (sources, assumptions, what was missing)'],
        ['DCF/LBO Architect', 'DCF and LBO starting from the valuation agent\'s shared assumptions; a deterministic LBO is added if the model omits one'],
        ['Risk Assessor / Legal Advisor / Red Team', 'Risk register, fiduciary and antitrust issues, adversarial "strategic silence" checks'],
        ['Investment Memo Agent', 'IC memo draft; grounded against evidence and reconciled against the other agents'],
    ])}
{note('', 'Agent confidence scores are model-reported and labelled <em>not calibrated</em> throughout the UI and reports. Treat them as signals, not probabilities.')}
"""
    write('docs/agents.html', shell('docs/agents.html', 'Agent catalog', body, 'All 29 DealForge AI agents with roles and tool allow-lists, generated from the code registry.'))


def page_tools():
    sections = ''
    for name, ids in TOOL_GROUPS:
        rows = [[f'<code>{i}</code>', e(TOOLS.get(i, '')[:230])] for i in ids]
        sections += f'<h3>{name} ({len(ids)})</h3>' + table(['Tool', 'What it does'], rows)
    body = f"""
<h1>Tools &amp; data sources</h1>
<p>Agents act through tools. 39 tools are registered; each agent may call only its own allow-list. Descriptions below come from the tool definitions.</p>
<h2>Data sources</h2>
{table(['Source', 'Used for', 'Key needed'], [
        ['SEC EDGAR (XBRL company facts + filing index)', 'Income statement, balance sheet and cash flow history with filing links; FCF = operating cash flow − capex', 'No'],
        ['Finnhub', 'Quotes, basic financials (beta, market cap, enterprise value), peer lists and peer multiples', 'Yes (free tier works)'],
        ['Alpha Vantage', 'Time series and technical indicators', 'Yes'],
        ['financial_datasets', 'Statements and market data', 'Yes'],
        ['Web search / scraper', 'News, competitor and market context', 'Depends on configured search backend'],
        ['Uploaded documents', 'Deal documents indexed locally (PageIndex) and searched with hybrid retrieval', 'No'],
        ['MCP servers', 'Read-only discovery and invocation of configured Streamable-HTTP MCP servers', 'Per server'],
    ])}
<h2>Tool catalog</h2>
{sections}
<h2>The text tool protocol</h2>
<p>Not every model supports native function calling. For those, agents use a text protocol: the model writes a fenced JSON block, the runtime executes it, and the result is fed back.</p>
{code('''{"command": "fetch_financial_statements",
 "args": {"ticker": "SPT", "statements": ["income", "balance", "cashflow"], "periods": 5}}''')}
<p>Rules enforced by the runtime: only tools on the agent's allow-list can run; arguments are type-coerced against the tool schema; every call is recorded with success/error; and the model is told it must never claim a source it did not call.</p>
<h2>Valuation inputs</h2>
<p>The valuation agent derives its inputs deterministically: unit-normalized financials (for example <code>…_millions_usd</code> keys), CAPM cost of equity with stated defaults (risk-free 4.25%, equity risk premium 5.0%, pre-tax cost of debt 7.0%, tax 25%, +1.0% small-cap premium under $2B market cap), peers filtered to 0.1×–10× the target's market cap, a 5-year FCF fade to 2.5% terminal growth. Everything it assumed, retrieved or could not retrieve is stored in an <em>audit</em> object that the UI and reports display.</p>
"""
    write('docs/tools-and-data.html', shell('docs/tools-and-data.html', 'Tools & data', body, 'The 39 tools available to DealForge AI agents and the data sources behind them.'))


def page_quality():
    p = '../'
    body = f"""
<h1>Quality &amp; controls</h1>
<p>Language models make confident mistakes. DealForge puts deterministic checks around them and shows the results to the user, instead of hoping the prompts are good enough.</p>
{table(['Control', 'What it does', 'Where'], [
        ['<strong>Approval gate</strong>', 'Plans are shown first; execution requires approve. The server rejects execution for a list that is not approved or whose dependencies are not done.', '<code>/chat/execute-task</code>, UI'],
        ['<strong>Dependency ordering</strong>', 'Data → modelling → synthesis; DCF/LBO agents wait for the valuation agent so they share its inputs.', '<code>project_manager.wire_stage_dependencies</code>'],
        ['<strong>Shared assumptions</strong>', 'WACC, terminal growth, horizon, starting growth, base FCF and market EV from the valuation audit are injected into every DCF/LBO prompt.', '<code>valuation_inputs.shared_assumptions_note</code>'],
        ['<strong>Evidence brief</strong>', 'All data points are collected with period, source link and basis (reported, derived, unverified). Duplicates across agents ("2025" vs "FY2025", rounded vs exact) collapse to one.', '<code>reports/evidence_brief.py</code>'],
        ['<strong>Memo grounding</strong>', 'Each figure in the memo is matched to recorded output; the grounded/total count is stored (for example 46 of 51) with the ungrounded figures listed. Citing a vendor that no tool returned is flagged.', '<code>validation/memo_grounding.py</code>'],
        ['<strong>Cross-agent reconciliation</strong>', 'Two DCF enterprise values more than 15% apart; an LBO entering below 95% of market EV or more than 60% above it; a DCF bridge whose PV of cash flows plus PV of terminal value is not within 5% of its enterprise value.', '<code>validation/reconciliation.py</code>'],
        ['<strong>Deterministic repair</strong>', 'If an LBO was requested but missing, one is built from sourced inputs; if a DCF bridge does not add up it is recomputed. Every assumption is listed in the output.', '<code>core/lbo.py</code>, <code>dcf_lbo_architect.py</code>'],
        ['<strong>Unusable-reply rejection</strong>', 'Empty, canned-error or off-topic short replies are failures: retried once and never stored as analysis or as the memo.', '<code>llm/gemini_client.py</code>, <code>investment_memo_agent.py</code>'],
        ['<strong>Tool-outcome recording</strong>', 'Success/failure per call, shown on the dashboard and available per agent result.', '<code>agents/base.py</code>'],
        ['<strong>Review gate</strong>', 'Reports carry a release status; unapproved reports download only as drafts (<code>_DRAFT_not_approved</code>, <code>X-Release-Status: draft-not-approved</code>), including from the chat Report button.', '<code>main.py</code>, <code>document_store.py</code>'],
        ['<strong>Provenance trail</strong>', 'Tool, parameters, raw response, timestamp and freshness per call; export at <code>/deals/{{id}}/provenance/export</code>.', '<code>core/provenance.py</code>'],
        ['<strong>Input guards</strong>', 'Prompt validation, JSON body size limit, upload path guards, optional admin bearer token for report endpoints.', '<code>chat_guard.py</code>, <code>path_guard.py</code>'],
    ])}
<h2>What a reconciliation flag looks like</h2>
<p>These are real warnings from a Sprout Social run, as shown on the dashboard row and in the Word report:</p>
{code('''Two DCFs differ by 23% ($675.0M vs $829.0M). Their WACC, growth and cash-flow
assumptions must be reconciled.
dcf_lbo_architect: PV of cash flows ($1.11B) plus PV of terminal value ($1.03B) is
$2.14B, not the stated enterprise value $829.0M. The DCF is internally inconsistent.''')}
<p>The second warning is now repaired automatically (the bridge is recomputed from the shared assumptions and the repair is noted). The first is intentionally <em>flagged, not forced</em>: two agents with different methodologies disagree, and a human decides which to trust.</p>
{fig(p, 'dashboard-pipeline', 'Review required: the chips and the amber bullets come from the checks above.')}
<h2>Deterministic LBO assumptions</h2>
<p>When the deterministic LBO is used, it states: entry at a 30% premium to market EV, 40% debt at 9% interest, 25% tax, 75% of post-interest cash flow sweeping the debt, five years, exit at the entry EV/FCF multiple (no multiple expansion), FCF growth fading from the observed rate to 2.5%. Change them in <code>backend/app/core/lbo.py</code>.</p>
<h2>What these checks cannot do</h2>
<ul><li>They verify consistency with recorded evidence, not truth: if a tool returned a wrong number, the number is "grounded".</li>
<li>Reconciliation compares enterprise values and bridges it can parse; models that bury figures under unfamiliar keys are not compared.</li>
<li>Model confidence is not calibrated, and the app says so.</li></ul>
"""
    write('docs/quality-and-controls.html', shell('docs/quality-and-controls.html', 'Quality & controls', body, 'How DealForge AI verifies agent output: grounding, reconciliation, deterministic repair, review gate and provenance.'))


def page_reports():
    p = '../'
    body = f"""
<h1>Reports &amp; review gate</h1>
<p>Reports are <strong>edited</strong>, not concatenated agent output. A deterministic pipeline turns persisted agent results into an evidence brief, an editorial layer trims and deduplicates, and renderers produce four formats.</p>
{flow([('Collect', 'Completed task results become <code>agent_results</code>; activity metadata is attached.'),
         ('Evidence brief', 'Data points with period, source and basis; source register (SEC filing index links); findings, gaps and metric conflicts.'),
         ('Editor', 'Key messages, per-agent summaries and a recommendation line. A deterministic fallback runs when no model is available. Unusable memos are flagged, not rendered.'),
         ('Render', 'python-docx, python-pptx, openpyxl and ReportLab/fpdf2, with Plotly charts (Matplotlib fallback).'),
         ('Validate', 'Structural checks on each artifact; text is cleaned (LaTeX markup, mojibake) before it reaches a document.'),
         ('Review gate', 'Each artifact carries <code>release_status</code>; clients download approved reports only.')])}
<h2>The four formats</h2>
{table(['Format', 'Typical contents'], [
        ['Word (.docx)', 'Cover, contents, <em>Evidence coverage &amp; review status</em>, metric reconciliation, executive summary with key messages, financial metrics with sources, per-agent findings, valuation and LBO tables, charts, full agent appendix, source register'],
        ['PowerPoint (.pptx)', '15 slides: title, executive summary, recorded metrics, revenue bridge, football field, valuation table, key messages, findings by agent, risks, takeaways, recommendation (clearly labelled as a draft), source register'],
        ['Excel (.xlsx)', '10 sheets: Executive Summary, Editor Brief, Income Statement, DCF Analysis, Comps, LBO Returns, Risk Matrix, Agent Analysis Detail, Sources &amp; References, Evidence Brief'],
        ['PDF', 'Concise 7-page version with the same key messages, evidence and charts'],
    ])}
<div class="shots">
<button class="shot" data-zoom="{p}assets/img/report-word-summary.webp"><img src="{p}assets/img/report-word-summary.webp" alt="Word key messages" loading="lazy"><span>Word: key messages and agent summaries</span></button>
<button class="shot" data-zoom="{p}assets/img/report-deck-football.webp"><img src="{p}assets/img/report-deck-football.webp" alt="Football field slide" loading="lazy"><span>PowerPoint: football field with market EV and LBO entry</span></button>
<button class="shot" data-zoom="{p}assets/img/report-deck-final.webp"><img src="{p}assets/img/report-deck-final.webp" alt="Recommendation slide" loading="lazy"><span>PowerPoint: recommendation labelled as an unapproved draft</span></button>
<button class="shot" data-zoom="{p}assets/img/report-excel-lbo.webp"><img src="{p}assets/img/report-excel-lbo.webp" alt="LBO sheet" loading="lazy"><span>Excel: LBO Returns from the recorded model</span></button>
<button class="shot" data-zoom="{p}assets/img/report-pdf.webp"><img src="{p}assets/img/report-pdf.webp" alt="PDF page" loading="lazy"><span>PDF: key messages and agent findings</span></button>
<button class="shot" data-zoom="{p}assets/img/report-deck-findings.webp"><img src="{p}assets/img/report-deck-findings.webp" alt="Findings slide" loading="lazy"><span>PowerPoint: findings by agent</span></button>
</div>
<h2>Release states</h2>
{table(['State', 'Meaning', 'Download behavior'], [
        ['<code>pending_review</code>', 'Generated, not approved', 'Plain download returns 409. Draft download (<code>?draft=true</code>) returns the file named <code>…_DRAFT_not_approved</code> with <code>X-Release-Status: draft-not-approved</code>'],
        ['<code>approved</code>', 'A reviewer approved the report set', 'Normal download'],
    ])}
<h3>Endpoints</h3>
{code('''POST /api/v1/deals/{id}/documents/plan       # preview the document plan (optional {"request": "...", "fill_gaps": true})
POST /api/v1/deals/{id}/documents/generate   # build docx, pptx, xlsx, pdf
POST /api/v1/deals/{id}/documents/approve    # reviewer approval (admin token if enforced)
GET  /api/v1/deals/{id}/documents            # artifact list with release status
GET  /api/v1/deals/{id}/documents/{fmt}      # fmt = docx | pptx | xlsx | pdf   (?draft=true)
GET  /api/v1/deals/{id}/documents/bundle     # all four
GET  /api/v1/deals/{id}/exports/docx         # on-the-fly Word report (what the chat Report button uses)
GET  /api/v1/deals/{id}/exports/json         # structured analysis export''')}
{note('warn', 'The recommendation in a report is the <strong>memo agent draft</strong>, shown with a label that it is model-generated and needs reviewer sign-off. DealForge does not compute an approved "deal score" in the chat flow; reports say "deal not scored" rather than invent one.')}
"""
    write('docs/reports.html', shell('docs/reports.html', 'Reports & review gate', body, 'How DealForge AI builds Word, PowerPoint, Excel and PDF reports from agent results and gates their release.'))


def page_api():
    def sect(title, rows):
        return f'<h2>{title}</h2>' + table(['Method', 'Path', 'Purpose'], [[f'<code>{m}</code>', f'<code>{pth}</code>', d] for m, pth, d in rows])
    body = f"""
<h1>API reference</h1>
<p>The backend exposes about 100 routes under <code>/api/v1</code>. Interactive OpenAPI docs are served at <code>/docs</code> on a running backend. Report-generation, download and approval endpoints honour <code>ADMIN_API_TOKEN</code> (send <code>Authorization: Bearer …</code>) when enforcement is on.</p>
{sect('Chat pipeline', [
        ('POST', '/chat/clarify', 'Data-need analysis and clarifying questions (≤3 rounds; <code>user_skipped</code> proceeds)'),
        ('POST', '/chat/clarify/feedback', 'Store Q&amp;A pairs and outcome scores in clarification memory'),
        ('POST', '/chat/plan', 'Create the task plan (todo list) for a deal'),
        ('POST', '/chat/execute-task', 'Run one planned task with its assigned agent'),
        ('POST', '/chat/respond', 'Conversational answer for follow-up questions'),
        ('POST', '/scrum/clarify · /scrum/plan', 'Scrum Master endpoints (same agent, direct)'),
        ('GET', '/stream/events/{deal_id}', 'Server-sent events for live progress'),
        ('POST', '/agents/run · GET /agents', 'Run or list registered agents'),
    ])}
{sect('Tasks (plans)', [
        ('GET', '/tasks/all · /tasks/{list_id}', 'List plans / fetch one'),
        ('GET · POST', '/deals/{deal_id}/tasks', 'Plans for a deal / create one'),
        ('PUT · DELETE', '/tasks/{list_id}/items/{task_id}', 'Edit or delete a task'),
        ('POST', '/tasks/{list_id}/items', 'Add a task'),
        ('POST', '/tasks/{list_id}/reorder', 'Reorder tasks'),
        ('POST', '/tasks/{list_id}/approve · /execute', 'Approve a plan / execute it'),
        ('PATCH', '/tasks/{list_id}/status', 'Set plan status'),
    ])}
{sect('Deals, results &amp; dashboard', [
        ('GET · POST', '/deals', 'List / create deals'),
        ('PATCH', '/deals/{deal_id}', 'Update status, stage or score'),
        ('GET', '/deals/{deal_id}/status · /results · /report · /agent-messages', 'Progress, results and messages'),
        ('POST', '/deals/{deal_id}/run', 'Run the autonomous LangGraph deal orchestrator'),
        ('GET', '/dashboard/metrics', 'KPIs, deals (with valuation/sourcing/review insights), activity feed'),
        ('POST', '/agent-activity', 'Record an agent activity event'),
        ('GET', '/deals/{deal_id}/provenance · /provenance/export', 'Tool-call audit trail'),
        ('GET', '/deals/{deal_id}/knowledge-graph', 'Current knowledge-graph facts for a deal'),
        ('GET · PUT · PATCH · DELETE', '/conversations…', 'Persisted chat conversations'),
    ])}
{sect('Documents &amp; exports', [
        ('POST', '/deals/{id}/documents/plan · /generate · /approve', 'Plan, build, approve'),
        ('GET', '/deals/{id}/documents · /documents/{fmt} · /documents/bundle', 'List, download, bundle'),
        ('GET', '/deals/{id}/exports/docx · /exports/json', 'On-the-fly Word report; structured data'),
        ('POST', '/documents/upload · /upload/bulk · /url · /directory', 'Index documents into the knowledge base'),
        ('POST', '/documents/query · /merge · /batch · /validate', 'Query, merge, batch-generate, validate'),
        ('GET', '/pageindex/documents · /pageindex/stats', 'Knowledge base inventory'),
        ('GET', '/knowledge/search · /status · POST /knowledge/ingest', 'Hybrid knowledge search and ingestion'),
    ])}
{sect('Models, settings &amp; integrations', [
        ('GET · POST', '/settings', 'Read (masked) / update provider settings'),
        ('GET', '/models/available · /models/routing', 'Provider catalog and per-agent routing'),
        ('POST', '/models/test', 'Test a provider connection'),
        ('GET', '/llm/usage · /gateway/usage', 'Usage and limits'),
        ('POST', '/gateway/call · /gateway/hybrid · /gateway/limits', 'Gateway calls and limit control'),
        ('GET · POST', '/mcp/status · /tools · /providers · /search · /initialize', 'External MCP servers (read-only)'),
        ('GET', '/laya/status · /skills/list · /startups/search', 'Laya router status, skills, startup search'),
    ])}
{sect('Specialist workflows', [
        ('POST', '/ofas/mission · GET /ofas/mission/{deal_id}/status · /monitor', 'OFAS RACI mission and monitoring'),
        ('POST', '/ofas/mission/{deal_id}/review-gate · /recover', 'Human review gate and error recovery'),
        ('POST', '/workflows/cv-simulation', 'GP-led continuation vehicle waterfall simulation'),
        ('POST', '/workflows/lmt-scan · /workflows/monte-carlo', 'Liability-management loophole scan; Monte Carlo recovery'),
    ])}
<h2>Example: drive the chat pipeline with curl</h2>
{code('''API=http://localhost:8005/api/v1
DEAL=$(uuidgen)

curl -s $API/chat/clarify -H 'content-type: application/json' -d '{"prompt":"Evaluate a take-private of Sprout Social (NASDAQ: SPT). Do not ask clarifying questions.","deal_id":"'$DEAL'"}'
curl -s $API/chat/plan    -H 'content-type: application/json' -d '{"prompt":"…","deal_id":"'$DEAL'"}'
# review the returned todo_list, then approve and run each task:
curl -s -X POST $API/tasks/$LIST_ID/approve
curl -s $API/chat/execute-task -H 'content-type: application/json' \\
     -d '{"agent_type":"data_curator","task":"…","deal_id":"'$DEAL'","task_list_id":"'$LIST_ID'","task_id":"'$TASK_ID'"}' ''')}
<p>The exact request bodies are visible in the browser network tab and in the interactive <code>/docs</code> page.</p>
"""
    write('docs/api.html', shell('docs/api.html', 'API reference', body, 'DealForge AI REST API: chat pipeline, tasks, deals, documents, models, MCP and specialist workflows.'))


def page_status():
    body = f"""
<h1>Status &amp; limits</h1>
<p>An honest account of what has been exercised and what has not.</p>
<h2>Verified end to end</h2>
<ul>
<li>The interactive chat pipeline: clarify → plan → approve → execute → memo → brief, with real SEC EDGAR and Finnhub data, through an OpenAI-compatible endpoint, on a real company (Sprout Social).</li>
<li>Dashboard metrics and per-deal insights, the task board, and generation of all four report formats with the review gate.</li>
<li>448 automated tests (backend), a TypeScript build, and visual checks of the UI and reports.</li>
</ul>
<h2>Implemented but less exercised</h2>
<ul>
<li>The LangGraph deal orchestrator, OFAS missions, the debate engine, confidence and user-input gates, HaluGate NLI checks, GP-led CV and LMT workflows. They are in the repository and API-exposed, but the demos and docs here do not claim them as verified.</li>
<li>Neo4j as the knowledge-graph backend, the cloud PageIndex mode, and MCP integrations depend on external services.</li>
</ul>
<h2>Known limits</h2>
{table(['Limit', 'Detail'], [
        ['Redis is required', 'There is no in-memory fallback for deals, conversations and artifacts'],
        ['Single-tenant', 'No user accounts; an optional admin bearer token guards report endpoints'],
        ['Model quality varies', 'Chat-style proxy models can return canned errors or off-topic text; these are detected and retried, but output quality still depends on the model'],
        ['Agents can still disagree', 'DCF assumptions are shared via the prompt, not forced; disagreement above 15% is flagged for a human, not resolved automatically'],
        ['Comps are only as good as the peer list', 'Finnhub peers can be loosely related; the valuation agent filters by size and uses the median, but peer selection is not curated'],
        ['No calibrated confidence', 'Confidence numbers are model-reported and labelled as such'],
        ['No deal score in the chat flow', 'Reports say "deal not scored" rather than invent one; the memo recommendation is a labelled draft'],
        ['Run time', 'A full run with a remote chat-style model takes several minutes; local models vary'],
        ['US-centric data', 'SEC EDGAR XBRL covers US filers; other markets need other sources'],
        ['Windows test failures', 'Two tests fail only on Windows (symlink permission, hard-coded paths)'],
    ])}
<h2>Data and privacy</h2>
<p>The site and the app make no calls to analytics services. The app sends prompts and tool outputs to whichever LLM provider you configure, and requests to the data APIs you enable. Run local models and the knowledge base stays on your machine. The demos use public company data only.</p>
<h2>Not financial advice</h2>
<p>DealForge produces analysis drafts. It is not an investment adviser, and its outputs must be reviewed by qualified people before any decision.</p>
"""
    write('docs/status.html', shell('docs/status.html', 'Status & limits', body, 'What DealForge AI has verified, what is experimental, and its known limits.'))


if __name__ == '__main__':
    page_index()
    page_getting_started()
    page_product_tour()
    page_architecture()
    page_agents()
    page_tools()
    page_quality()
    page_reports()
    page_api()
    page_status()
    print('site written to', ROOT)
