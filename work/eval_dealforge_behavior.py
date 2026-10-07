from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any


API_BASE = "http://127.0.0.1:8005"
FRONTEND_BASE = "http://localhost:3000"
ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "outputs"


def request_json(method: str, url: str, payload: dict[str, Any] | None = None, timeout: int = 30) -> tuple[int, Any, float]:
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            elapsed = time.perf_counter() - start
            try:
                return resp.status, json.loads(raw), elapsed
            except json.JSONDecodeError:
                return resp.status, raw, elapsed
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        elapsed = time.perf_counter() - start
        try:
            return exc.code, json.loads(raw), elapsed
        except json.JSONDecodeError:
            return exc.code, raw, elapsed
    except Exception as exc:
        elapsed = time.perf_counter() - start
        return 0, {"error": type(exc).__name__, "message": str(exc)}, elapsed


def request_text(url: str, timeout: int = 10) -> tuple[int, str, float]:
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace"), time.perf_counter() - start
    except Exception as exc:
        return 0, f"{type(exc).__name__}: {exc}", time.perf_counter() - start


def passed(name: str, ok: bool, details: str, elapsed: float, raw: Any = None) -> dict[str, Any]:
    return {
        "name": name,
        "passed": ok,
        "elapsed_s": round(elapsed, 3),
        "details": details,
        "raw": raw,
    }


def summarize_plan(plan: dict[str, Any], registered_agents: set[str]) -> tuple[bool, str]:
    todo = (((plan or {}).get("data") or {}).get("todo_list") or {})
    items = todo.get("items") or []
    assigned = [item.get("assigned_agent") for item in items if item.get("assigned_agent")]
    missing = sorted({agent for agent in assigned if agent not in registered_agents})
    required_workstreams = ["financial", "market", "legal", "risk", "valuation", "memo"]
    text = json.dumps(todo).lower()
    coverage = [term for term in required_workstreams if term in text]
    ok = len(items) >= 8 and not missing and len(coverage) >= 5
    detail = f"{len(items)} tasks; coverage={coverage}; unknown_agents={missing}"
    return ok, detail


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results: list[dict[str, Any]] = []

    code, html, elapsed = request_text(FRONTEND_BASE, timeout=10)
    results.append(passed("frontend_reachable", code == 200 and "root" in html.lower(), f"HTTP {code}; {len(html)} bytes", elapsed))

    code, health, elapsed = request_json("GET", f"{API_BASE}/health", timeout=10)
    results.append(passed("api_health", code == 200 and health.get("status") == "healthy", f"HTTP {code}; status={health.get('status')}", elapsed, health))

    code, agents_payload, elapsed = request_json("GET", f"{API_BASE}/api/v1/agents", timeout=15)
    registered_agents = set(agents_payload.get("agents") or []) if isinstance(agents_payload, dict) else set()
    results.append(
        passed(
            "agent_registry",
            code == 200 and len(registered_agents) >= 20,
            f"HTTP {code}; registered_agents={len(registered_agents)}",
            elapsed,
            agents_payload,
        )
    )

    code, routing, elapsed = request_json("GET", f"{API_BASE}/api/v1/models/routing", timeout=20)
    health_table = routing.get("health") or {} if isinstance(routing, dict) else {}
    unhealthy = sorted([name for name, row in health_table.items() if not row.get("is_healthy")])
    results.append(
        passed(
            "model_routing_health",
            code == 200 and not unhealthy and len(health_table) >= 20,
            f"HTTP {code}; providers={len(health_table)}; unhealthy={unhealthy}",
            elapsed,
            {"strategy": routing.get("strategy"), "unhealthy": unhealthy} if isinstance(routing, dict) else routing,
        )
    )

    code, invalid, elapsed = request_json(
        "POST",
        f"{API_BASE}/api/v1/chat/clarify",
        {"prompt": "", "deal_id": "eval-empty", "company_name": "EvalCo"},
        timeout=30,
    )
    results.append(
        passed(
            "empty_prompt_guardrail",
            code == 200 and invalid.get("error") == "invalid_prompt",
            f"HTTP {code}; error={invalid.get('error')}",
            elapsed,
            invalid,
        )
    )

    plan_payload = {
        "prompt": (
            "Build a diligence plan for acquiring EvalSaaS, a B2B SaaS company with "
            "$25M ARR, 80% growth, 9% churn, negative EBITDA, and enterprise customers. "
            "Focus on commercial, financial, legal, and AI product risk."
        ),
        "deal_id": f"eval-plan-{stamp}",
        "company_name": "EvalSaaS",
        "user_answers": [],
    }
    code, plan, elapsed = request_json("POST", f"{API_BASE}/api/v1/chat/plan", plan_payload, timeout=180)
    plan_ok, plan_detail = summarize_plan(plan if isinstance(plan, dict) else {}, registered_agents)
    results.append(passed("planning_quality", code == 200 and plan_ok, f"HTTP {code}; {plan_detail}", elapsed, plan))

    agent_payload = {
        "agent_type": "financial_analyst",
        "task": "Assess revenue quality, margin risk, and valuation concerns for a B2B SaaS acquisition. Return concise sections.",
        "context": {
            "target_company": "EvalSaaS",
            "industry": "B2B SaaS",
            "revenue_arr": "$25M",
            "growth": "80% YoY",
            "gross_margin": "72%",
            "net_revenue_retention": "118%",
            "churn": "9%",
            "ebitda_margin": "-18%",
            "debt": "$4M",
        },
    }
    code, agent, elapsed = request_json("POST", f"{API_BASE}/api/v1/agents/run", agent_payload, timeout=180)
    response_text = json.dumps(agent, ensure_ascii=False).lower() if isinstance(agent, dict) else str(agent).lower()
    useful_terms = ["revenue", "margin", "valuation", "churn", "arr"]
    useful_hits = [term for term in useful_terms if term in response_text]
    agent_has_content = bool((agent.get("data") if isinstance(agent, dict) else None) or (agent.get("reasoning") if isinstance(agent, dict) else None))
    results.append(
        passed(
            "financial_agent_usefulness",
            code == 200 and bool(agent.get("success")) and agent_has_content and len(useful_hits) >= 3,
            f"HTTP {code}; success={agent.get('success') if isinstance(agent, dict) else None}; content={agent_has_content}; useful_hits={useful_hits}",
            elapsed,
            agent,
        )
    )

    passed_count = sum(1 for item in results if item["passed"])
    score = round(passed_count / len(results), 3)
    report = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "api_base": API_BASE,
        "frontend_base": FRONTEND_BASE,
        "score": score,
        "passed": passed_count,
        "total": len(results),
        "results": results,
    }

    json_path = OUTPUT_DIR / f"dealforge_behavior_eval_{stamp}.json"
    md_path = OUTPUT_DIR / f"dealforge_behavior_eval_{stamp}.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "# DealForge Behavior and AI Eval",
        "",
        f"- Timestamp: {report['timestamp']}",
        f"- Frontend: {FRONTEND_BASE}",
        f"- API: {API_BASE}",
        f"- Score: {passed_count}/{len(results)} ({score:.0%})",
        "",
        "## Results",
        "",
    ]
    for item in results:
        mark = "PASS" if item["passed"] else "FAIL"
        lines.append(f"- {mark} `{item['name']}` ({item['elapsed_s']}s): {item['details']}")
    lines.extend(
        [
            "",
            "## Key Interpretation",
            "",
            "- A green health endpoint only proves the server is alive; the agent usefulness check verifies whether the AI layer returns usable content.",
            "- Planning quality includes both task coverage and whether assigned agents are executable registered agent IDs.",
            "- Raw payloads are preserved in the JSON report for debugging.",
        ]
    )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps({"score": score, "passed": passed_count, "total": len(results), "json": str(json_path), "md": str(md_path)}, indent=2))
    return 0 if passed_count == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
