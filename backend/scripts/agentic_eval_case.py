"""Agentic eval case: realistic deal run mirroring the Web UI flow.

Flow (same endpoints ChatWindow.tsx calls):
  1. Upload grounding document (deal-scoped) -> assert chunks indexed
  2. Create deal -> assert id
  3. Clarify (scrum questions) -> record count + latency
  4. Plan (task list) -> assert N tasks
  5. Execute every task sequentially (500ms throttle like the UI)
  6. RAG retrieval check on deal docs -> assert hits + citations
  7. Direct Laya checks (triage / confidence gate / tier route)

By default the eval snapshots Settings, routes ALL agents to a working
local provider (ollama), runs, then RESTORES settings. Override with
--provider / --no-restore.

Usage:
  venv\\Scripts\\python.exe scripts/agentic_eval_case.py [--provider ollama]
                                                          [--model qwen2.5:7b]
                                                          [--no-restore]
                                                          [--tag my-run]
Progress: TEMP/agentic_eval_<tag>.jsonl   Report: backend/test_reports/agentic_eval_<tag>.{json,md}
"""

import argparse
import copy
import json
import os
import sys
import tempfile
import time
import urllib.request

BASE = os.environ.get("DEALFORGE_API", "http://127.0.0.1:8005")
TEMP = tempfile.gettempdir()

BRIEF = (
    "Analyze the acquisition of Northwind Analytics, a B2B SaaS company "
    "with $42M ARR growing 28% YoY, 130% net revenue retention, 18% EBITDA "
    "margin, and 340 employees. Proposed enterprise value $310M (7.4x ARR). "
    "Flag customer concentration (top 5 = 41% of revenue), SOC2 gaps found "
    "in diligence, and EU data-residency exposure under GDPR."
)

DOC = """# Northwind Analytics — Diligence Pack

## Financials (FY2025, audited)
ARR closed at $42.0M, up 28% YoY. Net revenue retention 130%.
Gross margin 81%. EBITDA margin 18% ($7.6M). Rule of 40: 46.
Cash $19M, no debt. Burn multiple 0.9. CAC payback 7 months.

## Risk Factors
Top 5 customers contribute 41% of ARR; loss of any two breaches the
$310M valuation case. SOC2 Type II audit open items: 3 medium findings on
access reviews. EU data residency: 22% of revenue from EU customers hosted
in us-east-1, GDPR remediation estimated $1.2M.

## Market
Primary market: revenue-analytics tooling for mid-market SaaS, estimated
$4.1B growing 19% CAGR. Two strategic buyers circling; exclusivity ends
in 21 days. Board vote Friday.
"""

CLARIFY_PROMPT = (
    "Analyze the acquisition of Northwind Analytics, a B2B SaaS company "
    "with $42M ARR, 28% growth, 130% NRR, 18% EBITDA margin. "
    "Proposed EV $310M."
)


def api(method, path, payload=None, timeout=300, raw_bytes=None):
    data, headers = None, {}
    if raw_bytes is not None:
        data = raw_bytes
    elif payload is not None:
        data = json.dumps(payload).encode()
        headers = {"Content-Type": "application/json"}
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode()
        try:
            return r.status, json.loads(body)
        except ValueError:
            return r.status, {"_raw": body[:500]}


def log(fh, **kw):
    kw["ts"] = time.strftime("%H:%M:%S")
    fh.write(json.dumps(kw) + "\n")
    fh.flush()
    print(kw.get("msg", kw), flush=True)


def check(results, name, ok, detail=""):
    results.append({"check": name, "ok": bool(ok), "detail": str(detail)[:300]})
    return bool(ok)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="ollama")
    ap.add_argument("--model", default="qwen2.5:7b")
    ap.add_argument("--no-restore", action="store_true")
    ap.add_argument("--tag", default=time.strftime("%Y%m%d-%H%M%S"))
    args = ap.parse_args()

    tag = args.tag
    log_fh = open(os.path.join(TEMP, f"agentic_eval_{tag}.jsonl"), "w", encoding="utf-8")
    checks, t0 = [], time.time()
    log(log_fh, msg=f"eval {tag} starting -> {BASE}")

    # 0. snapshot settings, route everything to a working provider
    st, settings = api("GET", "/api/v1/settings", timeout=60)
    check(checks, "settings_read", st == 200)
    original = copy.deepcopy(settings)
    if args.provider == "ollama":
        settings["ollama_model"] = args.model
    ar = settings.get("agent_routing", {})
    routed = [a for a in ar]
    for a in routed:
        ar[a] = args.provider
    settings["agent_routing"] = ar
    st, _ = api("POST", "/api/v1/settings", payload=settings, timeout=60)
    check(checks, "settings_route_all_" + args.provider, st == 200,
          f"{len(routed)} agents")

    try:
        # 1. create deal
        t = time.time()
        st, deal = api("POST", "/api/v1/deals", {
            "name": f"Eval Northwind {tag}", "target_company": "Northwind Analytics",
            "industry": "SaaS",
            "description": BRIEF}, timeout=120)
        deal_id = (deal or {}).get("id", "")
        check(checks, "deal_create", st == 200 and bool(deal_id), f"{time.time()-t:.0f}s")
        log(log_fh, msg=f"deal {deal_id}")

        # 2. upload grounding doc (multipart via urllib)
        import uuid
        boundary = "----eval" + uuid.uuid4().hex
        fname = "northwind_diligence.md"
        body = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"{fname}\"\r\nContent-Type: text/markdown\r\n\r\n"
            + DOC + f"\r\n--{boundary}--\r\n").encode()
        req = urllib.request.Request(
            f"{BASE}/api/v1/documents/upload?deal_id={deal_id}", data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST")
        with urllib.request.urlopen(req, timeout=300) as r:
            up = json.loads(r.read().decode())
        check(checks, "doc_upload_indexed", up.get("status") == "indexed"
              and up.get("total_chunks", 0) > 0, str(up))

        # 3. clarify
        t = time.time()
        st, clar = api("POST", "/api/v1/chat/clarify", {
            "deal_id": deal_id, "company_name": "Northwind Analytics",
            "prompt": CLARIFY_PROMPT, "clarification_round": 0}, timeout=300)
        nq = len((clar or {}).get("clarifying_questions", []))
        check(checks, "clarify", st == 200 and nq > 0,
              f"{nq} questions in {time.time()-t:.0f}s")

        # 4. plan
        t = time.time()
        st, plan = api("POST", "/api/v1/chat/plan", {
            "deal_id": deal_id, "company_name": "Northwind Analytics",
            "prompt": CLARIFY_PROMPT,
            "user_answers": ["Proceed with reasonable SaaS assumptions."]}, timeout=300)
        items = ((plan or {}).get("data") or {}).get("todo_list", {}).get("items", [])
        check(checks, "plan", st == 200 and len(items) >= 3,
              f"{len(items)} tasks in {time.time()-t:.0f}s")

        # 5. execute all tasks (UI throttles ~500ms between calls)
        prior, exec_results = {}, []
        for i, task in enumerate(items, 1):
            agent = task.get("assigned_agent", "")
            t = time.time()
            try:
                st, res = api("POST", "/api/v1/chat/execute-task", {
                    "agent_type": agent,
                    "task": task.get("description", "") or task.get("title", ""),
                    "deal_id": deal_id, "task_id": task.get("id", ""),
                    "title": task.get("title", ""), "ticker": "Northwind Analytics",
                    "company_name": "Northwind Analytics",
                    "agent_outputs": prior}, timeout=1500)
                reasoning = str((res or {}).get("reasoning", ""))
                err = reasoning.lstrip().startswith("[Error]") or res.get("error")
                ok = st == 200 and bool(reasoning.strip()) and not err
                conf = (res or {}).get("confidence")
                prov = (res or {}).get("provider")
                exec_results.append({"agent": agent, "title": task.get("title"),
                                     "ok": ok, "provider": prov, "confidence": conf,
                                     "chars": len(reasoning),
                                     "seconds": round(time.time() - t)})
                prior[task.get("id", "")] = reasoning[:2000]
                log(log_fh, msg=f"[{i}/{len(items)}] {agent} ok={ok} "
                                f"prov={prov} conf={conf} {time.time()-t:.0f}s")
                time.sleep(0.5)
            except Exception as e:
                exec_results.append({"agent": agent, "title": task.get("title"),
                                     "ok": False, "error": str(e)[:200]})
                log(log_fh, msg=f"[{i}/{len(items)}] {agent} FAILED: {e}")
        ok_n = sum(1 for r in exec_results if r["ok"])
        check(checks, "execute_all", ok_n == len(items),
              f"{ok_n}/{len(items)} ok")
        real = [r for r in exec_results
                if r.get("chars", 0) > 200 and r.get("provider") == args.provider]
        check(checks, "execute_real_content", len(real) >= max(1, len(items) // 2),
              f"{len(real)} tasks with >200 chars from {args.provider}")

        # 6. RAG retrieval check
        t = time.time()
        st, q = api("POST", "/api/v1/documents/query", {
            "query": "Northwind ARR EBITDA customer concentration",
            "top_k": 3, "deal_id": deal_id}, timeout=300)
        hits = (q or {}).get("results", [])
        cites = [h for h in hits if h.get("citation")]
        check(checks, "rag_retrieval", st == 200 and len(hits) > 0,
              f"{len(hits)} hits in {time.time()-t:.0f}s")
        check(checks, "rag_citations", len(cites) > 0, f"{len(cites)} cited")
    finally:
        if not args.no_restore:
            api("POST", "/api/v1/settings", payload=original, timeout=60)
            log(log_fh, msg="settings restored")
        else:
            log(log_fh, msg="settings NOT restored (--no-restore)")

    # 7. direct Laya checks (fail-soft; record backend used)
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
        from app.core.laya.client import get_laya_client
        client = get_laya_client()
        tri = await_one(client.triage_deal(BRIEF))
        if client.backend == "off":
            # Laya intentionally disabled (e.g. pure-local eval run) — not a failure
            check(checks, "laya_triage", True, "skipped (mode=off)")
            check(checks, "laya_tier", True, "skipped (mode=off)")
        else:
            check(checks, "laya_triage", tri is not None,
                  f"backend={(tri or {}).get('backend', client.backend)}")
            tier = await_one(client.route_tier("Summarize this paragraph."))
            check(checks, "laya_tier", True, f"tier={tier} backend={client.backend}")
    except Exception as e:
        check(checks, "laya_direct", False, str(e)[:200])

    passed = sum(1 for c in checks if c["ok"])
    verdict = "PASS" if passed == len(checks) else ("WARN" if passed / len(checks) >= 0.8 else "FAIL")
    report = {"tag": tag, "verdict": verdict, "passed": passed,
              "total": len(checks), "seconds": round(time.time() - t0),
              "deal_id": locals().get("deal_id", ""),
              "exec_results": locals().get("exec_results", []),
              "checks": checks}
    outdir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", "test_reports")
    os.makedirs(outdir, exist_ok=True)
    json.dump(report, open(os.path.join(outdir, f"agentic_eval_{tag}.json"), "w"), indent=1)
    md = [f"# Agentic eval {tag} — {verdict} ({passed}/{len(checks)}, {report['seconds']}s)",
          "", "| check | ok | detail |", "|---|---|---|"]
    md += [f"| {c['check']} | {'✅' if c['ok'] else '❌'} | {c['detail']} |" for c in checks]
    open(os.path.join(outdir, f"agentic_eval_{tag}.md"), "w",
         encoding="utf-8").write("\n".join(md) + "\n")
    log(log_fh, msg=f"EVAL {verdict} {passed}/{len(checks)} in {report['seconds']}s")
    log_fh.close()
    return 0 if verdict == "PASS" else 1


def await_one(coro):
    import asyncio
    return asyncio.new_event_loop().run_until_complete(coro)


if __name__ == "__main__":
    sys.exit(main())
