"""
GhostBusters — GCP Detection Engine
Reads a SADA billing CSV (service-level GCP report) and runs waste / risk detectors.
Output: gcp_findings.json
"""

import pandas as pd
import json
import os
import re
from datetime import datetime

# ─── Thresholds ──────────────────────────────────────────────────────────────
SPIKE_PCT_THRESHOLD   = 10.0   # % MoM increase → flag as spike
DROP_PCT_THRESHOLD    = 50.0   # % MoM decrease → investigate sharp drop
SUPPORT_PCT_OF_SPEND  = 3.0    # Support > 3% of total = excessive
AI_SERVICES = {                # GCP AI/LLM service keywords
    "claude", "gemini", "vertex ai", "vertex", "dialogflow",
    "natural language", "vision api", "speech api", "translation",
}
CUD_ELIGIBLE = {               # Services that can use Committed Use Discounts
    "compute engine", "cloud sql", "kubernetes engine",
    "cloud run", "cloud spanner", "bigtable",
}
LOGGING_SPIKE_USD     = 5000   # Cloud Logging above this monthly = review retention

# ─── CSV loader ───────────────────────────────────────────────────────────────

def load_sada_csv(filepath: str) -> pd.DataFrame:
    """
    Parse a SADA GCP billing CSV. Strips summary rows (Subtotal/Tax/Total).
    Returns a clean DataFrame with numeric columns coerced.
    """
    df = pd.read_csv(filepath)
    # Drop summary rows (Service description is blank or is a subtotal marker)
    df = df[df["Service description"].notna() & (df["Service description"].str.strip() != "")]
    # Drop any row where Service ID is blank (summary lines)
    if "Service ID" in df.columns:
        df = df[df["Service ID"].notna() & (df["Service ID"].str.strip() != "")]

    # Rename for convenience
    df = df.rename(columns={
        "Service description":                            "service",
        "Service ID":                                     "service_id",
        "List cost ($)":                                  "list_cost",
        "Negotiated savings ($)":                         "negotiated_savings",
        "Savings programs ($)":                           "savings_programs",
        "Other savings ($)":                              "other_savings",
        "Unrounded subtotal ($)":                         "unrounded_subtotal",
        "Subtotal ($)":                                   "subtotal",
        "Percent change in subtotal compared to previous period": "pct_change_raw",
    })

    # Coerce numeric
    for col in ["list_cost","negotiated_savings","savings_programs","other_savings","subtotal"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    # Parse percent change — "29%", "-2%", "New", "0%"
    def parse_pct(v):
        v = str(v).strip()
        if v.lower() == "new":
            return None          # new service, no prior period
        m = re.search(r"[-\d.]+", v)
        return float(m.group()) if m else None

    df["pct_change"] = df["pct_change_raw"].apply(parse_pct)
    df["is_new"]     = df["pct_change_raw"].str.strip().str.lower() == "new"
    df["total_savings"] = (
        df["negotiated_savings"].abs() +
        df["savings_programs"].abs() +
        df["other_savings"].abs()
    )
    return df.reset_index(drop=True)


# ─── Detectors ────────────────────────────────────────────────────────────────

def detect_spend_spikes(df: pd.DataFrame) -> list[dict]:
    """Flag services with MoM increase above threshold."""
    findings = []
    for _, row in df.iterrows():
        pct = row["pct_change"]
        if pct is None or pct <= SPIKE_PCT_THRESHOLD:
            continue
        findings.append({
            "detector":    "spend_spike",
            "category":    "Cost Spike",
            "service":     row["service"],
            "service_id":  row["service_id"],
            "monthly_cost": round(row["subtotal"], 2),
            "pct_change":  pct,
            "severity":    "HIGH" if pct > 20 else "MEDIUM",
            "plain_english": (
                f"{row['service']} spend grew {pct:+.0f}% month-over-month to "
                f"${row['subtotal']:,.2f}/mo. This is above the {SPIKE_PCT_THRESHOLD}% "
                f"acceptable variance threshold."
            ),
            "monthly_opportunity": 0,
            "priority_action": (
                f"Investigate what drove the {pct:+.0f}% increase in {row['service']}. "
                f"Check for new workloads, quota increases, or billing anomalies in GCP Console → Billing → Cost breakdown."
            ),
        })
    return sorted(findings, key=lambda x: -x["pct_change"])


def detect_sharp_drops(df: pd.DataFrame) -> list[dict]:
    """Flag services with >50% MoM drop — may indicate unintentional shutdowns."""
    findings = []
    for _, row in df.iterrows():
        pct = row["pct_change"]
        if pct is None or pct >= -DROP_PCT_THRESHOLD:
            continue
        if row["subtotal"] < 10:   # too small to care
            continue
        findings.append({
            "detector":    "sharp_drop",
            "category":    "Anomaly",
            "service":     row["service"],
            "service_id":  row["service_id"],
            "monthly_cost": round(row["subtotal"], 2),
            "pct_change":  pct,
            "severity":    "MEDIUM",
            "plain_english": (
                f"{row['service']} dropped {abs(pct):.0f}% MoM to ${row['subtotal']:,.2f}/mo. "
                f"Large drops can indicate accidental shutdowns, missing workloads, or billing credit anomalies."
            ),
            "monthly_opportunity": 0,
            "priority_action": (
                f"Verify that the drop in {row['service']} is intentional. "
                f"Check GCP Console → Billing → Cost table for credits or terminated resources."
            ),
        })
    return sorted(findings, key=lambda x: x["pct_change"])


def detect_ai_spend(df: pd.DataFrame, total_spend: float) -> list[dict]:
    """Aggregate AI/LLM services and flag if they represent a large share of spend."""
    findings = []
    ai_rows = df[df["service"].str.lower().apply(
        lambda s: any(kw in s for kw in AI_SERVICES)
    )]
    if ai_rows.empty:
        return findings

    ai_total  = ai_rows["subtotal"].sum()
    ai_pct    = round(ai_total / total_spend * 100, 1) if total_spend else 0
    ai_list   = ai_rows.sort_values("subtotal", ascending=False)[["service","subtotal","pct_change"]].to_dict("records")

    # Flag the top AI spender separately
    top = ai_rows.loc[ai_rows["subtotal"].idxmax()]
    top_pct = top.get("pct_change", 0) or 0

    findings.append({
        "detector":        "ai_spend",
        "category":        "AI / LLM Cost",
        "service":         "AI/LLM Services (aggregated)",
        "service_id":      "AGGREGATED",
        "monthly_cost":    round(ai_total, 2),
        "pct_change":      None,
        "severity":        "HIGH" if ai_pct > 15 else "MEDIUM",
        "plain_english": (
            f"AI and LLM services account for ${ai_total:,.2f}/mo ({ai_pct}% of total GCP spend). "
            f"Top spender: {top['service']} at ${top['subtotal']:,.2f}/mo "
            f"({top_pct:+.0f}% vs last month)."
        ),
        "monthly_opportunity": 0,
        "priority_action": (
            "Review AI API usage logs for unused or test calls. "
            "Implement request caching, prompt compression, and consider smaller models "
            "for non-critical workloads. Set budget alerts at 80% of expected AI spend."
        ),
        "breakdown": ai_list,
    })

    # Flag each AI service that's spiking
    for _, row in ai_rows.iterrows():
        pct = row["pct_change"]
        if pct and pct > SPIKE_PCT_THRESHOLD and row["subtotal"] > 100:
            findings.append({
                "detector":        "ai_spike",
                "category":        "AI / LLM Cost",
                "service":         row["service"],
                "service_id":      row["service_id"],
                "monthly_cost":    round(row["subtotal"], 2),
                "pct_change":      pct,
                "severity":        "HIGH" if pct > 25 else "MEDIUM",
                "plain_english": (
                    f"{row['service']} grew {pct:+.0f}% MoM to ${row['subtotal']:,.2f}/mo. "
                    f"Unchecked AI API growth can quickly become the largest cost driver."
                ),
                "monthly_opportunity": round(row["subtotal"] * 0.25, 2),
                "priority_action": (
                    f"Audit {row['service']} call volume. Add rate limits, caching, "
                    f"and model-tier routing (use smaller/cheaper models for drafts). "
                    f"Set a GCP budget alert on this service."
                ),
            })
    return findings


def detect_cud_opportunity(df: pd.DataFrame) -> list[dict]:
    """Flag CUD-eligible services with low/no committed use discounts."""
    findings = []
    for _, row in df.iterrows():
        svc_lower = row["service"].lower()
        if not any(kw in svc_lower for kw in CUD_ELIGIBLE):
            continue
        savings_programs_abs = abs(row["savings_programs"])
        list_cost = row["list_cost"]
        if list_cost < 500:
            continue
        cud_coverage = savings_programs_abs / list_cost if list_cost else 0
        if cud_coverage > 0.40:   # already has good CUD coverage
            continue
        potential = round(list_cost * 0.30, 2)   # conservative 30% CUD saving estimate
        findings.append({
            "detector":        "cud_opportunity",
            "category":        "Reserved / Committed Use",
            "service":         row["service"],
            "service_id":      row["service_id"],
            "monthly_cost":    round(row["subtotal"], 2),
            "pct_change":      row["pct_change"],
            "severity":        "HIGH" if list_cost > 5000 else "MEDIUM",
            "plain_english": (
                f"{row['service']} has a list cost of ${list_cost:,.2f}/mo with only "
                f"{cud_coverage*100:.0f}% covered by Committed Use Discounts. "
                f"Purchasing 1-year CUDs could save ~30%."
            ),
            "monthly_opportunity": potential,
            "priority_action": (
                f"Purchase 1-year Committed Use Discounts for {row['service']} in "
                f"GCP Console → Billing → Committed use discounts. "
                f"Estimated saving: ${potential:,.2f}/mo."
            ),
        })
    return sorted(findings, key=lambda x: -x["monthly_opportunity"])


def detect_excessive_support(df: pd.DataFrame, total_spend: float) -> list[dict]:
    """Flag if Support cost exceeds threshold % of total spend."""
    findings = []
    support_rows = df[df["service"].str.lower().str.contains("support")]
    if support_rows.empty:
        return findings
    support_cost = support_rows["subtotal"].sum()
    support_pct  = round(support_cost / total_spend * 100, 1) if total_spend else 0
    if support_pct < SUPPORT_PCT_OF_SPEND:
        return findings
    findings.append({
        "detector":        "excessive_support",
        "category":        "Support Overhead",
        "service":         "Support",
        "service_id":      support_rows.iloc[0]["service_id"],
        "monthly_cost":    round(support_cost, 2),
        "pct_change":      support_rows.iloc[0]["pct_change"],
        "severity":        "MEDIUM",
        "plain_english": (
            f"Support charges are ${support_cost:,.2f}/mo ({support_pct}% of total GCP spend). "
            f"This exceeds the {SUPPORT_PCT_OF_SPEND}% benchmark for a well-optimised account."
        ),
        "monthly_opportunity": round(support_cost * 0.20, 2),
        "priority_action": (
            "Review SADA support tier vs. actual tickets opened. "
            "Consider downgrading support tier if ticket volume is low, "
            "or consolidating support contracts across GCP projects."
        ),
    })
    return findings


def detect_logging_costs(df: pd.DataFrame) -> list[dict]:
    """Flag high Cloud Logging costs — often driven by verbose log sinks."""
    findings = []
    log_rows = df[df["service"].str.lower().str.contains("logging")]
    if log_rows.empty:
        return findings
    log_cost = log_rows["subtotal"].sum()
    if log_cost < LOGGING_SPIKE_USD:
        return findings
    findings.append({
        "detector":        "logging_costs",
        "category":        "Log Retention",
        "service":         "Cloud Logging",
        "service_id":      log_rows.iloc[0]["service_id"],
        "monthly_cost":    round(log_cost, 2),
        "pct_change":      log_rows.iloc[0]["pct_change"],
        "severity":        "MEDIUM",
        "plain_english": (
            f"Cloud Logging costs ${log_cost:,.2f}/mo. "
            f"Verbose application logs, audit logs, and VPC flow logs often account for "
            f"60-70% of this spend — much of it stored indefinitely."
        ),
        "monthly_opportunity": round(log_cost * 0.60, 2),
        "priority_action": (
            "In GCP Console → Logging → Log Router, exclude high-volume low-value logs "
            "(DEBUG, INFO for non-critical services). Apply 30-day retention to all non-audit "
            "log buckets. Archive to Cloud Storage for compliance if needed."
        ),
    })
    return findings


def detect_new_services(df: pd.DataFrame) -> list[dict]:
    """Flag brand-new services that appeared this billing period."""
    findings = []
    new_rows = df[df["is_new"] == True]
    for _, row in new_rows.iterrows():
        findings.append({
            "detector":        "new_service",
            "category":        "New Service",
            "service":         row["service"],
            "service_id":      row["service_id"],
            "monthly_cost":    round(row["subtotal"], 2),
            "pct_change":      None,
            "severity":        "LOW",
            "plain_english": (
                f"{row['service']} appeared for the first time this billing period "
                f"at ${row['subtotal']:,.2f}. "
                f"New services should be reviewed to ensure they are intentional and owned."
            ),
            "monthly_opportunity": 0,
            "priority_action": (
                f"Confirm {row['service']} was intentionally enabled. "
                f"Assign an owner, add budget alerts, and tag the associated GCP project."
            ),
        })
    return findings


def detect_unused_savings(df: pd.DataFrame) -> list[dict]:
    """
    Flag large services (>$1000/mo list cost) with zero savings programs applied
    that are CUD-eligible — they are leaving money on the table.
    """
    findings = []
    for _, row in df.iterrows():
        if row["list_cost"] < 1000:
            continue
        if abs(row["savings_programs"]) > 0:
            continue
        svc_lower = row["service"].lower()
        if not any(kw in svc_lower for kw in CUD_ELIGIBLE):
            continue
        potential = round(row["list_cost"] * 0.30, 2)
        findings.append({
            "detector":        "zero_savings_programs",
            "category":        "Reserved / Committed Use",
            "service":         row["service"],
            "service_id":      row["service_id"],
            "monthly_cost":    round(row["subtotal"], 2),
            "pct_change":      row["pct_change"],
            "severity":        "HIGH" if row["list_cost"] > 5000 else "MEDIUM",
            "plain_english": (
                f"{row['service']} has a list cost of ${row['list_cost']:,.2f}/mo "
                f"with $0 in Savings Programs applied. "
                f"Purchasing CUDs could save ~30% (~${potential:,.2f}/mo)."
            ),
            "monthly_opportunity": potential,
            "priority_action": (
                f"Evaluate 1-year or 3-year Committed Use Discounts for "
                f"{row['service']} to reduce on-demand pricing."
            ),
        })
    return sorted(findings, key=lambda x: -x["monthly_opportunity"])


# ─── Main pipeline ────────────────────────────────────────────────────────────

def run_gcp_detection(csv_path: str, output_path: str = "gcp_findings.json"):
    print(f"[GhostBusters GCP] Loading {csv_path}")
    df = load_sada_csv(csv_path)

    total_spend      = round(df["subtotal"].sum(), 2)
    total_list_cost  = round(df["list_cost"].sum(), 2)
    total_savings    = round(df["total_savings"].sum(), 2)
    savings_pct      = round(total_savings / total_list_cost * 100, 1) if total_list_cost else 0
    period_start     = csv_path  # embed filename as source reference

    print(f"[GhostBusters GCP] {len(df)} services, total spend: ${total_spend:,.2f}")

    # Run all detectors
    all_findings: list[dict] = []
    all_findings += detect_spend_spikes(df)
    all_findings += detect_sharp_drops(df)
    all_findings += detect_ai_spend(df, total_spend)
    all_findings += detect_cud_opportunity(df)
    all_findings += detect_excessive_support(df, total_spend)
    all_findings += detect_logging_costs(df)
    all_findings += detect_new_services(df)
    all_findings += detect_unused_savings(df)

    # De-duplicate: keep highest severity per service per detector type
    seen = set()
    deduped = []
    for f in all_findings:
        key = (f["detector"], f["service"])
        if key not in seen:
            seen.add(key)
            deduped.append(f)

    # Rank by monthly_opportunity desc, then by severity
    sev_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    deduped.sort(key=lambda x: (-x["monthly_opportunity"], sev_order.get(x["severity"], 3)))
    for i, f in enumerate(deduped, 1):
        f["rank"] = i

    # Services list for dashboard charts
    services_list = df.sort_values("subtotal", ascending=False)[
        ["service","service_id","list_cost","subtotal","total_savings","pct_change","pct_change_raw","is_new"]
    ].to_dict("records")

    total_opportunity = round(sum(f.get("monthly_opportunity", 0) for f in deduped), 2)

    summary = {
        "cloud":                "GCP",
        "source":               os.path.basename(csv_path),
        "generated_at":         datetime.now().isoformat(),
        "total_monthly_spend":  total_spend,
        "total_list_cost":      total_list_cost,
        "total_savings_applied":total_savings,
        "savings_efficiency_pct": savings_pct,
        "total_monthly_opportunity": total_opportunity,
        "total_annual_opportunity":  round(total_opportunity * 12, 2),
        "total_findings":       len(deduped),
        "services":             services_list,
        "findings":             deduped,
        "waste_by_category": {
            cat: round(sum(f["monthly_opportunity"] for f in deduped if f["category"] == cat), 2)
            for cat in set(f["category"] for f in deduped)
        },
    }

    with open(output_path, "w") as fh:
        json.dump(summary, fh, indent=2)

    print(f"[GhostBusters GCP] {len(deduped)} findings, ${total_opportunity:,.2f}/mo opportunity → {output_path}")
    return summary


if __name__ == "__main__":
    sada_csv = os.environ.get(
        "GHOSTBUSTERS_GCP_CSV",
        "Perforce Software, Inc. - SADA_Reports, 2026-05-01 \u2014 2026-05-26.csv"
    )
    out_path = os.environ.get("GHOSTBUSTERS_GCP_FINDINGS", "gcp_findings.json")
    run_gcp_detection(sada_csv, out_path)
