"""
GhostBusters — GCP LLM Analyzer
Sends gcp_findings.json to Claude and produces gcp_report.json
"""

import json
import re
import os
import urllib.request
from datetime import datetime


def load_gcp_findings(filepath: str = "gcp_findings.json") -> dict:
    with open(filepath) as f:
        return json.load(f)


def build_gcp_prompt(data: dict) -> str:
    total_spend  = data["total_monthly_spend"]
    total_list   = data["total_list_cost"]
    total_saved  = data["total_savings_applied"]
    efficiency   = data["savings_efficiency_pct"]
    opportunity  = data["total_monthly_opportunity"]
    n_findings   = data["total_findings"]
    source       = data["source"]

    top_findings = data["findings"][:12]
    findings_txt = json.dumps(top_findings, indent=2)

    # Build AI spend summary
    ai_findings = [f for f in data["findings"] if "AI" in f.get("category","") or "claude" in f.get("service","").lower() or "gemini" in f.get("service","").lower()]
    ai_txt = json.dumps(ai_findings, indent=2) if ai_findings else "none"

    return f"""You are a senior FinOps engineer and GCP cloud cost analyst at Perforce.
You are analyzing a SADA billing report for the period covered by: {source}

ACCOUNT SUMMARY:
- Cloud provider: GCP (via SADA reseller)
- Total monthly spend (after discounts): ${total_spend:,.2f}
- Total list cost (before any discounts): ${total_list:,.2f}
- Total savings applied by SADA: ${total_saved:,.2f} ({efficiency}% discount rate)
- Additional optimization opportunity identified: ${opportunity:,.2f}/mo
- Total findings: {n_findings}
- Waste by category: {json.dumps(data.get("waste_by_category", {}))}

TOP FINDINGS (ranked by opportunity):
{findings_txt}

AI/LLM SPEND:
{ai_txt}

Respond ONLY with a valid JSON object — no preamble, no markdown fences. Use this exact schema:
{{
  "executive_summary": "3-sentence summary a CTO would read. Include total GCP spend, what SADA already saves, remaining opportunity, and biggest risk.",
  "total_monthly_spend": <number>,
  "total_monthly_opportunity": <number>,
  "total_annual_opportunity": <number>,
  "savings_already_applied": <number>,
  "findings": [
    {{
      "rank": <number>,
      "service": "<string>",
      "category": "<string>",
      "severity": "HIGH|MEDIUM|LOW",
      "monthly_cost": <number>,
      "monthly_opportunity": <number>,
      "plain_english": "2-sentence explanation of the issue and why it costs money. No jargon.",
      "business_impact": "1 sentence on the business risk if left unaddressed.",
      "priority_action": "One concrete action the team should take this week.",
      "gcp_action": "<the exact GCP Console path or gcloud CLI command to fix it>"
    }}
  ],
  "quick_wins": ["3 GCP-specific actions the team can take today that require zero downtime"],
  "ai_spend_insight": "2-sentence insight specifically about AI/LLM spend (Claude, Vertex, Gemini). Include trend and recommendation.",
  "sada_savings_assessment": "1-sentence assessment of how well SADA negotiated discounts and whether there is room to push for more.",
  "service_breakdown": {{
    "biggest_concern": "<service name and why>",
    "most_improved": "<service with best trend>",
    "watch_list": ["<service1>", "<service2>", "<service3>"]
  }},
  "closing_recommendation": "2-sentence closing advice for engineering leadership on GCP cost governance."
}}"""


def call_claude(prompt: str) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not set. Run: export ANTHROPIC_API_KEY='sk-ant-...'")

    payload = json.dumps({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 4000,
        "messages": [{"role": "user", "content": prompt}]
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode())
    return data["content"][0]["text"]


def extract_json(raw: str) -> dict:
    raw = raw.strip()
    # Strip markdown fences if present
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw)


def analyze(findings_path: str = "gcp_findings.json", output_path: str = "gcp_report.json"):
    print(f"[GhostBusters GCP Analyzer] Loading {findings_path}")
    data = load_gcp_findings(findings_path)

    # Pass raw findings data through to report
    prompt = build_gcp_prompt(data)

    print("[GhostBusters GCP Analyzer] Calling Claude...")
    raw = call_claude(prompt)

    report = extract_json(raw)

    # Enrich with raw data for dashboard charts
    report["source"]          = data.get("source", "SADA GCP Billing Report")
    report["cloud"]           = "GCP"
    report["generated_at"]    = datetime.now().isoformat()
    report["raw_services"]    = data.get("services", [])
    report["all_findings"]    = data.get("findings", [])
    report["total_list_cost"] = data.get("total_list_cost", 0)
    report["savings_efficiency_pct"] = data.get("savings_efficiency_pct", 0)

    with open(output_path, "w") as fh:
        json.dump(report, fh, indent=2)

    print(f"[GhostBusters GCP Analyzer] Report written to {output_path}")
    print(f"  Total spend:       ${report.get('total_monthly_spend',0):,.2f}/mo")
    print(f"  Opportunity:       ${report.get('total_monthly_opportunity',0):,.2f}/mo")
    print(f"  Annual opportunity:${report.get('total_annual_opportunity',0):,.2f}")
    return report


if __name__ == "__main__":
    findings_path = os.environ.get("GHOSTBUSTERS_GCP_FINDINGS", "gcp_findings.json")
    output_path   = os.environ.get("GHOSTBUSTERS_GCP_REPORT",   "gcp_report.json")
    analyze(findings_path, output_path)
