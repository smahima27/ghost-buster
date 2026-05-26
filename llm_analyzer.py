import json
import re
import os
import urllib.request
from datetime import datetime

# ─── Load findings ────────────────────────────────────────────────────────────

def load_findings(filepath="findings.json"):
    with open(filepath) as f:
        return json.load(f)

# ─── Build prompt ─────────────────────────────────────────────────────────────

def build_prompt(summary, top_findings):
    findings_text = json.dumps(top_findings, indent=2)
    return f"""You are a senior FinOps engineer and cloud cost analyst.
Analyze the following AWS infrastructure waste findings and produce a structured audit report.

SUMMARY:
- Total findings: {summary['total_findings']}
- Total monthly waste: ${summary['total_monthly_waste']:,.2f}
- Total annual waste: ${summary['total_annual_waste']:,.2f}
- Waste by category: {json.dumps(summary['waste_by_category'])}

TOP 10 WASTE FINDINGS (ranked by severity and cost):
{findings_text}

Respond ONLY with a valid JSON object — no preamble, no markdown fences. Use this exact schema:
{{
  "executive_summary": "3-sentence summary a CTO would read. Include total waste, top offender, and urgency.",
  "total_monthly_waste": <number>,
  "total_annual_waste": <number>,
  "findings": [
    {{
      "rank": <number>,
      "finding_id": "<string>",
      "resource_name": "<string>",
      "team": "<string>",
      "plain_english": "2-sentence explanation of what this resource is doing wrong and why it costs money. No jargon.",
      "business_impact": "1 sentence on the business risk or cost impact if left unaddressed.",
      "monthly_saving": <number>,
      "priority_action": "One concrete action the team should take this week.",
      "cli_fix": "<the exact AWS CLI command to fix it>"
    }}
  ],
  "quick_wins": ["3 actions the team can take today that require zero downtime"],
  "team_breakdown": {{
    "<team_name>": {{
      "monthly_waste": <number>,
      "top_issue": "<string>"
    }}
  }},
  "closing_recommendation": "2-sentence closing advice for the engineering leadership."
}}"""

# ─── Call Claude API ──────────────────────────────────────────────────────────

def call_claude(prompt):
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY environment variable not set. Run: export ANTHROPIC_API_KEY='sk-ant-...'")

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
            "anthropic-version": "2023-06-01"
        },
        method="POST"
    )

    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode())

    raw = data["content"][0]["text"].strip()
    raw = re.sub(r"^```json\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw)

# ─── Main ─────────────────────────────────────────────────────────────────────

def analyze(findings_path="findings.json"):
    data    = load_findings(findings_path)
    summary = data["summary"]
    top10   = data["top_findings"]

    print("Building prompt...")
    prompt = build_prompt(summary, top10)

    print("Calling Claude API for analysis...")
    report = call_claude(prompt)

    report["generated_at"] = datetime.today().strftime("%Y-%m-%d %H:%M")
    report["all_findings"] = data["all_findings"]
    report["raw_top10"]    = top10

    with open("llm_report.json", "w") as f:
        json.dump(report, f, indent=2)

    # ─── Pretty print ─────────────────────────────────────────────────────────
    print("\n" + "="*60)
    print("  CLOUD COST WASTE HUNTER — AI ANALYSIS REPORT")
    print("="*60)
    print(f"\n  EXECUTIVE SUMMARY\n  {report['executive_summary']}\n")
    print(f"  Monthly waste : ${report['total_monthly_waste']:,.2f}")
    print(f"  Annual waste  : ${report['total_annual_waste']:,.2f}")

    print("\n" + "-"*60)
    print("  TOP 10 FINDINGS\n")
    for f in report["findings"]:
        print(f"  #{f['rank']}  {f['resource_name']}  (team: {f['team']})")
        print(f"      {f['plain_english']}")
        print(f"      Impact  : {f['business_impact']}")
        print(f"      Saving  : ${f['monthly_saving']:,.2f}/mo")
        print(f"      Action  : {f['priority_action']}")
        print(f"      CLI     : {f['cli_fix']}")
        print()

    print("-"*60)
    print("  QUICK WINS — Do these today\n")
    for i, w in enumerate(report["quick_wins"], 1):
        print(f"  {i}. {w}")

    print("\n" + "-"*60)
    print("  WASTE BY TEAM\n")
    for team, info in report.get("team_breakdown", {}).items():
        print(f"  {team:<15} ${info['monthly_waste']:>8,.2f}/mo  — {info['top_issue']}")

    print("\n" + "-"*60)
    print(f"\n  RECOMMENDATION\n  {report['closing_recommendation']}")
    print("\n" + "="*60)
    print("  llm_report.json written.")

    return report

if __name__ == "__main__":
    import os
    findings_path = os.environ.get("GHOSTBUSTERS_FINDINGS", "findings.json")
    analyze(findings_path)
