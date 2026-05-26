import re
import os
import pandas as pd
import json
from datetime import datetime, timedelta

# ─── Config thresholds ───────────────────────────────────────────────────────
IDLE_CPU_THRESHOLD      = 5.0    # % — EC2/RDS avg CPU below this = idle
IDLE_DAYS_THRESHOLD     = 7      # days running with low CPU
EBS_UNATTACHED_DAYS     = 5      # days unattached before flagging
S3_COLD_DAYS            = 60     # days since last access = cold storage
EIP_FLAG_ALL            = True   # all unassociated EIPs are waste
RIGHTSIZE_CPU_THRESHOLD = 20.0   # % — EC2 below this = oversized

# Rightsizing map: current → recommended (one tier down)
RIGHTSIZE_MAP = {
    "t3.medium":   ("t3.small",    0.0208),
    "t3.large":    ("t3.medium",   0.0416),
    "m5.xlarge":   ("m5.large",    0.096),
    "m5.2xlarge":  ("m5.xlarge",   0.192),
    "m5.4xlarge":  ("m5.2xlarge",  0.384),
    "c5.4xlarge":  ("c5.2xlarge",  0.34),
    "c5.9xlarge":  ("c5.4xlarge",  0.68),
    "r5.2xlarge":  ("r5.xlarge",   0.252),
    "db.t3.medium":("db.t3.small", 0.034),
    "db.m5.large": ("db.t3.medium",0.068),
    "db.m5.xlarge":("db.m5.large", 0.171),
    "db.r5.2xlarge":("db.r5.xlarge",0.48),
}

# ─── Load data ────────────────────────────────────────────────────────────────

REQUIRED_COLUMNS = {
    "resource_id", "resource_name", "service", "resource_type",
    "region", "team", "environment", "cpu_avg_7d", "monthly_cost_usd",
    "days_running", "last_accessed", "status",
}


def _detect_csv_format(filepath: str) -> str:
    """Return 'inventory', 'billing_resource', or 'billing_service'."""
    with open(filepath, newline="", encoding="utf-8-sig") as f:
        first_line = f.readline()
    # Resource-level billing export: first cell is "Resource", columns are ARNs/IDs with ($)
    if first_line.lstrip("\ufeff").strip('"').startswith("Resource") and "($)" in first_line:
        return "billing_resource"
    # Service-level billing export: first cell is "Service", columns are service names with ($)
    if "Service" in first_line and "($)" in first_line:
        return "billing_service"
    return "inventory"


def load_data(filepath="aws_cost_data.csv"):
    if not os.path.exists(filepath):
        raise FileNotFoundError(
            f"\n  File not found: '{filepath}'\n"
            f"  Check the path and try again."
        )

    fmt = _detect_csv_format(filepath)

    if fmt == "billing_resource":
        # Cost Explorer resource-level export — run billing analysis instead
        print(f"  Detected format: AWS Cost Explorer resource-level export")
        print(f"  Running billing analysis mode...\n")
        run_billing_analysis(filepath)
        raise SystemExit(0)

    if fmt == "billing_service":
        raise ValueError(
            f"\n  Detected an AWS Cost Explorer service-level billing export in '{filepath}'.\n"
            f"  This format has service costs grouped by date and lacks per-resource\n"
            f"  metrics (CPU, size, team, etc.) needed for waste detection.\n\n"
            f"  Use 'aws_cost_data.csv' (the included sample) as a template for the\n"
            f"  resource inventory format this engine requires."
        )

    # Inventory format — validate required columns
    preview = pd.read_csv(filepath, nrows=0)
    actual_cols = set(preview.columns.str.strip())
    missing = REQUIRED_COLUMNS - actual_cols
    if missing:
        raise ValueError(
            f"\n  Incompatible CSV format in '{filepath}'.\n"
            f"  Missing required columns: {sorted(missing)}\n"
            f"  Found columns: {sorted(actual_cols)}\n\n"
            f"  Required columns: {sorted(REQUIRED_COLUMNS)}"
        )

    df = pd.read_csv(filepath, parse_dates=["last_accessed"])
    df["days_since_access"] = (datetime.today() - df["last_accessed"]).dt.days
    return df


# ─── Billing CSV analysis (Cost Explorer resource-level export) ───────────────

def _infer_service(resource_id: str) -> str:
    h = resource_id
    if re.match(r"^i-[0-9a-f]+$", h):                    return "EC2"
    if re.match(r"^vol-[0-9a-f]+$", h):                  return "EBS"
    if re.match(r"^snap-[0-9a-f]+$", h):                 return "EBS Snapshot"
    if re.match(r"^eipalloc-", h):                        return "Elastic IP"
    if re.match(r"^eni-", h):                             return "Network Interface"
    if re.match(r"^vpn-", h):                             return "VPN"
    if "cloudfront" in h:                                 return "CloudFront"
    if "elasticloadbalancing" in h:                       return "ELB"
    if "rds" in h or "docdb" in h:                        return "RDS"
    if "kinesis" in h:                                    return "Kinesis"
    if "logs" in h and "log-group" in h:                  return "CloudWatch Logs"
    if "lambda" in h:                                     return "Lambda"
    if "kms" in h:                                        return "KMS"
    if "elastic-ip" in h:                                 return "Elastic IP"
    if "route53" in h:                                    return "Route53"
    if "quicksight" in h:                                 return "QuickSight"
    if "arn:aws:s3:::" in h:                              return "S3"
    if "secretsmanager" in h:                             return "Secrets Manager"
    if "ecr" in h:                                        return "ECR"
    if "elasticfilesystem" in h:                          return "EFS"
    if "scheduler" in h:                                  return "EventBridge"
    if "sns" in h:                                        return "SNS"
    if "sqs" in h:                                        return "SQS"
    if "amplify" in h:                                    return "Amplify"
    if "cloudformation" in h:                             return "CloudFormation"
    return "Other"


def _extract_region_from_arn(arn: str) -> str:
    parts = arn.split(":")
    if len(parts) >= 4 and parts[3]:
        return parts[3]
    return "global"


def run_billing_analysis(filepath: str) -> None:
    """Parse a Cost Explorer resource-level billing CSV and write billing_report.json."""
    import csv as csv_mod
    from collections import defaultdict

    with open(filepath, newline="", encoding="utf-8-sig") as f:
        rows = list(csv_mod.reader(f))

    if len(rows) < 2:
        print("  Empty or unreadable file.")
        return

    headers  = rows[0]   # label + resource ARNs/IDs
    total_row = rows[1]  # "Resource total" + costs

    # Collect date rows (any row whose first cell looks like a date)
    date_rows = [r for r in rows[2:] if re.match(r"\d{4}-\d{2}-\d{2}", r[0].strip('"'))]
    dates = [r[0].strip('"') for r in date_rows]

    # Build S3 bucket name set from ARN-prefixed columns for cross-reference
    s3_bucket_names = {
        headers[i].strip('"').replace("arn:aws:s3:::", "").replace("($)", "").strip()
        for i in range(1, len(headers))
        if "arn:aws:s3:::" in headers[i]
    }

    resources = []
    for i in range(1, len(headers)):
        raw_name  = headers[i].strip('"').replace("($)", "").strip()
        cost_str  = total_row[i] if i < len(total_row) else "0"
        try:
            total_cost = float(cost_str)
        except ValueError:
            total_cost = 0.0

        # Skip the synthetic "Total costs" column
        if raw_name in ("Total costs", "No Resource Id"):
            continue

        # Determine service
        # S3: bare bucket names (no arn: prefix) that appear in the s3_bucket_names set
        if (raw_name in s3_bucket_names
                and not raw_name.startswith("arn:")
                and not re.match(r"^i-|^vol-|^vpn-|^snap-|^eipalloc-|^eni-", raw_name)):
            service = "S3"
            resource_id = raw_name
        else:
            service = _infer_service(raw_name)
            resource_id = raw_name

        # Skip ARN-prefixed S3 duplicates (they show $0 and are already counted above)
        if "arn:aws:s3:::" in raw_name:
            continue

        # Per-date costs (for last-active heuristic)
        daily_costs = []
        for dr in date_rows:
            try:
                daily_costs.append((dr[0].strip('"'), float(dr[i]) if i < len(dr) else 0.0))
            except (ValueError, IndexError):
                daily_costs.append((dr[0].strip('"'), 0.0))

        # Last date with non-zero cost
        active_dates = [d for d, c in daily_costs if c > 0]
        last_active  = active_dates[-1] if active_dates else (dates[-1] if dates else "unknown")
        first_active = active_dates[0]  if active_dates else (dates[0]  if dates else "unknown")

        region = _extract_region_from_arn(raw_name) if raw_name.startswith("arn:") else "us-east-1"
        monthly_cost = round(total_cost * 30, 2)

        resources.append({
            "resource_id":    resource_id,
            "service":        service,
            "region":         region,
            "daily_cost":     round(total_cost, 6),
            "monthly_cost":   monthly_cost,
            "last_active":    last_active,
            "first_active":   first_active,
            "date_range":     f"{dates[0]} → {dates[-1]}" if dates else "unknown",
        })

    resources.sort(key=lambda x: -x["monthly_cost"])

    # Service summary
    by_service: dict = defaultdict(lambda: {"count": 0, "monthly_cost": 0.0, "resources": []})
    for r in resources:
        by_service[r["service"]]["count"]        += 1
        by_service[r["service"]]["monthly_cost"] += r["monthly_cost"]
        by_service[r["service"]]["resources"].append(r)

    # S3 deep dive
    s3_resources = sorted(
        [r for r in resources if r["service"] == "S3"],
        key=lambda x: -x["monthly_cost"]
    )
    s3_zero = [r for r in s3_resources if r["monthly_cost"] == 0]
    s3_active = [r for r in s3_resources if r["monthly_cost"] > 0]

    total_monthly = round(sum(r["monthly_cost"] for r in resources), 2)
    s3_monthly    = round(sum(r["monthly_cost"] for r in s3_resources), 2)

    report = {
        "generated_at":    datetime.today().strftime("%Y-%m-%d %H:%M"),
        "source_file":     filepath,
        "date_range":      f"{dates[0]} → {dates[-1]}" if dates else "unknown",
        "total_resources": len(resources),
        "total_monthly_cost": total_monthly,
        "s3_summary": {
            "total_buckets":       len(s3_resources),
            "buckets_with_cost":   len(s3_active),
            "zero_cost_buckets":   len(s3_zero),
            "total_monthly_cost":  s3_monthly,
            "pct_of_total":        round(s3_monthly / total_monthly * 100, 1) if total_monthly else 0,
        },
        "service_breakdown": {
            svc: {"count": v["count"], "monthly_cost": round(v["monthly_cost"], 2)}
            for svc, v in sorted(by_service.items(), key=lambda x: -x[1]["monthly_cost"])
        },
        "top_s3_buckets":   s3_active[:20],
        "zero_cost_s3":     [r["resource_id"] for r in s3_zero],
        "top_resources":    resources[:20],
    }

    with open("billing_report.json", "w") as f:
        json.dump(report, f, indent=2)

    # ── Print summary ──────────────────────────────────────────────────────────
    print("=" * 60)
    print("  BILLING ANALYSIS — COST EXPLORER RESOURCE EXPORT")
    print("=" * 60)
    print(f"  Date range        : {report['date_range']}")
    print(f"  Total resources   : {report['total_resources']}")
    print(f"  Total monthly est : ${total_monthly:,.2f}")
    print()
    print("  Cost by service:")
    for svc, info in report["service_breakdown"].items():
        bar = "█" * min(int(info["monthly_cost"] / total_monthly * 30), 30) if total_monthly else ""
        print(f"    {svc:<25} ${info['monthly_cost']:>10,.2f}/mo  {bar}")
    print()
    print(f"  S3 BUCKET ANALYSIS  ({len(s3_resources)} buckets · ${s3_monthly:,.2f}/mo)")
    print(f"  {'Bucket':<50} {'Monthly':>10}")
    print(f"  {'-'*50} {'-'*10}")
    for b in s3_active[:15]:
        print(f"  {b['resource_id']:<50} ${b['monthly_cost']:>9,.2f}")
    if len(s3_active) > 15:
        print(f"  ... and {len(s3_active)-15} more buckets")
    if s3_zero:
        print(f"\n  {len(s3_zero)} buckets with $0 cost (potentially unused):")
        for b in s3_zero[:10]:
            print(f"    - {b['resource_id']}")
    print()
    print("  billing_report.json written.")
    print("=" * 60)

# ─── S3 helpers ─────────────────────────────────────────────────────────────────

def _parse_size_gb(resource_type: str) -> float:
    """Extract numeric GB from strings like 'Standard-1625GB'."""
    m = re.search(r'([\d.]+)GB', resource_type, re.IGNORECASE)
    return float(m.group(1)) if m else 0.0


def _s3_access_tier(days: int) -> str:
    """Classify S3 bucket by days since last access."""
    if days < 30:
        return "Active"
    elif days < 60:
        return "Infrequent"
    elif days < 90:
        return "Cold"
    return "Frozen"


# ─── Detection rules ──────────────────────────────────────────────────────────

def detect_idle_ec2(df):
    findings = []
    ec2 = df[df["service"] == "EC2"].copy()
    idle = ec2[
        (ec2["cpu_avg_7d"] < IDLE_CPU_THRESHOLD) &
        (ec2["days_running"] >= IDLE_DAYS_THRESHOLD)
    ]
    for _, r in idle.iterrows():
        findings.append({
            "finding_id":       f"IDLE-EC2-{r['resource_id'][-6:]}",
            "category":         "Idle Resource",
            "severity":         "HIGH" if r["monthly_cost_usd"] > 100 else "MEDIUM",
            "service":          "EC2",
            "resource_id":      r["resource_id"],
            "resource_name":    r["resource_name"],
            "region":           r["region"],
            "team":             r["team"],
            "environment":      r["environment"],
            "detail":           f"Instance running {r['days_running']}d with avg CPU {r['cpu_avg_7d']}% — well below {IDLE_CPU_THRESHOLD}% threshold",
            "monthly_waste_usd": float(r["monthly_cost_usd"]),
            "recommendation":   f"Stop or terminate {r['resource_id']}. If needed occasionally, convert to spot or use auto-start/stop scheduler.",
            "cli_fix":          f"aws ec2 stop-instances --instance-ids {r['resource_id']} --region {r['region']}"
        })
    return findings


def detect_unattached_ebs(df):
    findings = []
    ebs = df[
        (df["service"] == "EBS") &
        (df["status"].str.contains("unattached", case=False))
    ]
    for _, r in ebs.iterrows():
        findings.append({
            "finding_id":       f"EBS-UNATTACHED-{r['resource_id'][-6:]}",
            "category":         "Zombie Resource",
            "severity":         "HIGH" if r["monthly_cost_usd"] > 50 else "MEDIUM",
            "service":          "EBS",
            "resource_id":      r["resource_id"],
            "resource_name":    r["resource_name"],
            "region":           r["region"],
            "team":             r["team"],
            "environment":      r["environment"],
            "detail":           f"Volume unattached for {r['days_running']} days, accruing ${r['monthly_cost_usd']}/mo with zero utilization",
            "monthly_waste_usd": float(r["monthly_cost_usd"]),
            "recommendation":   "Snapshot then delete if data not needed. If needed, attach to an instance.",
            "cli_fix":          f"aws ec2 create-snapshot --volume-id {r['resource_id']} --description 'backup-before-delete' && aws ec2 delete-volume --volume-id {r['resource_id']}"
        })
    return findings


def detect_unassociated_eips(df):
    findings = []
    eips = df[df["service"] == "Elastic IP"]
    for _, r in eips.iterrows():
        findings.append({
            "finding_id":       f"EIP-UNUSED-{r['resource_id'][-6:]}",
            "category":         "Zombie Resource",
            "severity":         "LOW",
            "service":          "Elastic IP",
            "resource_id":      r["resource_id"],
            "resource_name":    r["resource_name"],
            "region":           r["region"],
            "team":             r["team"],
            "environment":      r["environment"],
            "detail":           f"Elastic IP unassociated for {r['days_running']} days. AWS charges $3.60/mo per idle EIP.",
            "monthly_waste_usd": float(r["monthly_cost_usd"]),
            "recommendation":   "Release EIP if no longer needed.",
            "cli_fix":          f"aws ec2 release-address --allocation-id {r['resource_id']} --region {r['region']}"
        })
    return findings


def detect_cold_s3(df):
    findings = []
    s3 = df[
        (df["service"] == "S3") &
        (df["days_since_access"] >= S3_COLD_DAYS)
    ]
    for _, r in s3.iterrows():
        saving = round(r["monthly_cost_usd"] * 0.55, 2)  # Glacier ~55% cheaper
        size_gb = _parse_size_gb(str(r["resource_type"]))
        tier = _s3_access_tier(int(r["days_since_access"]))
        findings.append({
            "finding_id":        f"S3-COLD-{r['resource_id'][-8:]}",
            "category":          "Storage Optimisation",
            "severity":          "MEDIUM",
            "service":           "S3",
            "resource_id":       r["resource_id"],
            "resource_name":     r["resource_name"],
            "region":            r["region"],
            "team":              r["team"],
            "environment":       r["environment"],
            "size_gb":           size_gb,
            "access_tier":       tier,
            "days_since_access": int(r["days_since_access"]),
            "last_accessed":     str(r["last_accessed"].date()),
            "detail":            f"{size_gb:,.0f} GB bucket not accessed in {r['days_since_access']} days but on S3 Standard pricing. Tier: {tier}.",
            "monthly_waste_usd": saving,
            "recommendation":    "Apply S3 Intelligent-Tiering or Lifecycle rule to move to Glacier after 30 days.",
            "cli_fix":           f"aws s3api put-bucket-lifecycle-configuration --bucket {r['resource_id']} --lifecycle-configuration file://glacier-lifecycle.json"
        })
    return findings


def detect_s3_infrequent_access(df):
    """Flag S3 buckets accessed 30-59 days ago — candidates for S3-IA tier."""
    findings = []
    s3 = df[
        (df["service"] == "S3") &
        (df["days_since_access"] >= 30) &
        (df["days_since_access"] < S3_COLD_DAYS)
    ]
    for _, r in s3.iterrows():
        saving = round(r["monthly_cost_usd"] * 0.45, 2)  # S3-IA ~45% cheaper
        size_gb = _parse_size_gb(str(r["resource_type"]))
        findings.append({
            "finding_id":        f"S3-IA-{r['resource_id'][-8:]}",
            "category":          "Storage Optimisation",
            "severity":          "LOW",
            "service":           "S3",
            "resource_id":       r["resource_id"],
            "resource_name":     r["resource_name"],
            "region":            r["region"],
            "team":              r["team"],
            "environment":       r["environment"],
            "size_gb":           size_gb,
            "access_tier":       "Infrequent",
            "days_since_access": int(r["days_since_access"]),
            "last_accessed":     str(r["last_accessed"].date()),
            "detail":            f"{size_gb:,.0f} GB bucket last accessed {r['days_since_access']} days ago. Move to S3-Infrequent Access tier.",
            "monthly_waste_usd": saving,
            "recommendation":    "Switch to S3 Infrequent Access or enable Intelligent-Tiering.",
            "cli_fix":           f"aws s3api put-bucket-intelligent-tiering-configuration --bucket {r['resource_id']} --id tiering-config --intelligent-tiering-configuration Id=tiering-config,Status=Enabled"
        })
    return findings


def detect_rightsizing(df):
    findings = []
    ec2_rds = df[df["service"].isin(["EC2", "RDS"])].copy()
    oversized = ec2_rds[
        (ec2_rds["cpu_avg_7d"] < RIGHTSIZE_CPU_THRESHOLD) &
        (ec2_rds["cpu_avg_7d"] >= IDLE_CPU_THRESHOLD)  # not idle, just oversized
    ]
    for _, r in oversized.iterrows():
        itype = r["resource_type"]
        if itype not in RIGHTSIZE_MAP:
            continue
        recommended, new_hourly = RIGHTSIZE_MAP[itype]
        saving = round(r["monthly_cost_usd"] - (new_hourly * 24 * 30), 2)
        if saving <= 0:
            continue
        findings.append({
            "finding_id":       f"RIGHTSIZE-{r['resource_id'][-6:]}",
            "category":         "Rightsizing",
            "severity":         "MEDIUM",
            "service":          r["service"],
            "resource_id":      r["resource_id"],
            "resource_name":    r["resource_name"],
            "region":           r["region"],
            "team":             r["team"],
            "environment":      r["environment"],
            "detail":           f"{itype} running at {r['cpu_avg_7d']}% avg CPU. Rightsizing to {recommended} saves ${saving}/mo.",
            "monthly_waste_usd": saving,
            "recommendation":   f"Downsize from {itype} to {recommended}. Schedule during maintenance window.",
            "cli_fix":          f"aws ec2 modify-instance-attribute --instance-id {r['resource_id']} --instance-type {{Value={recommended}}}"
        })
    return findings


def detect_idle_rds(df):
    findings = []
    rds = df[
        (df["service"] == "RDS") &
        (df["cpu_avg_7d"] < IDLE_CPU_THRESHOLD) &
        (df["days_running"] >= IDLE_DAYS_THRESHOLD)
    ]
    for _, r in rds.iterrows():
        findings.append({
            "finding_id":       f"IDLE-RDS-{r['resource_id'][-6:]}",
            "category":         "Idle Resource",
            "severity":         "HIGH",
            "service":          "RDS",
            "resource_id":      r["resource_id"],
            "resource_name":    r["resource_name"],
            "region":           r["region"],
            "team":             r["team"],
            "environment":      r["environment"],
            "detail":           f"RDS instance idle for {r['days_running']} days with {r['cpu_avg_7d']}% CPU. RDS cannot be stopped for more than 7 days without auto-restart.",
            "monthly_waste_usd": float(r["monthly_cost_usd"]),
            "recommendation":   "Take final snapshot and delete if unused. For dev/test, use Aurora Serverless v2 which scales to zero.",
            "cli_fix":          f"aws rds create-db-snapshot --db-instance-identifier {r['resource_id']} --db-snapshot-identifier {r['resource_id']}-final-snap"
        })
    return findings


# ─── Scoring & ranking ────────────────────────────────────────────────────────

SEVERITY_MULTIPLIER = {"HIGH": 1.5, "MEDIUM": 1.0, "LOW": 0.6}

def score_and_rank(findings, top_n=10):
    for f in findings:
        f["waste_score"] = round(
            f["monthly_waste_usd"] * SEVERITY_MULTIPLIER[f["severity"]], 2
        )
    ranked = sorted(findings, key=lambda x: x["waste_score"], reverse=True)
    for i, f in enumerate(ranked):
        f["rank"] = i + 1
    return ranked[:top_n]


# ─── Summary ──────────────────────────────────────────────────────────────────

def build_summary(all_findings, top10):
    total_waste = sum(f["monthly_waste_usd"] for f in all_findings)
    by_category = {}
    for f in all_findings:
        by_category.setdefault(f["category"], 0)
        by_category[f["category"]] += f["monthly_waste_usd"]

    return {
        "generated_at":         datetime.today().strftime("%Y-%m-%d %H:%M"),
        "total_findings":       len(all_findings),
        "total_monthly_waste":  round(total_waste, 2),
        "total_annual_waste":   round(total_waste * 12, 2),
        "waste_by_category":    {k: round(v, 2) for k, v in sorted(by_category.items(), key=lambda x: -x[1])},
        "top10_monthly_waste":  round(sum(f["monthly_waste_usd"] for f in top10), 2),
    }


# ─── S3 deep-dive analysis ────────────────────────────────────────────────────

def _build_s3_analysis(df):
    """Build a rich S3 analysis dataset with size, tier, termination flags."""
    s3 = df[df["service"] == "S3"].copy()
    rows = []
    for _, r in s3.iterrows():
        size_gb     = _parse_size_gb(str(r["resource_type"]))
        tier        = _s3_access_tier(int(r["days_since_access"]))
        terminate   = (tier == "Frozen") and (r["environment"] in ("dev", "sandbox"))
        if tier == "Frozen":
            saving = round(r["monthly_cost_usd"] * 0.55, 2)
            recommendation = "Delete bucket (dev/sandbox) or archive to Glacier"
        elif tier == "Cold":
            saving = round(r["monthly_cost_usd"] * 0.55, 2)
            recommendation = "Move to S3 Glacier via Lifecycle rule"
        elif tier == "Infrequent":
            saving = round(r["monthly_cost_usd"] * 0.45, 2)
            recommendation = "Switch to S3-Infrequent Access or Intelligent-Tiering"
        else:
            saving = 0.0
            recommendation = "No action needed — actively used"
        rows.append({
            "resource_id":      r["resource_id"],
            "resource_name":    r["resource_name"],
            "region":           r["region"],
            "team":             r["team"],
            "environment":      r["environment"],
            "size_gb":          size_gb,
            "storage_class":    str(r["resource_type"]).split("-")[0],
            "last_accessed":    str(r["last_accessed"].date()),
            "days_since_access": int(r["days_since_access"]),
            "access_tier":      tier,
            "monthly_cost_usd": float(r["monthly_cost_usd"]),
            "potential_saving": saving,
            "terminate_candidate": terminate,
            "recommendation":   recommendation,
            "cli_fix": (
                f"aws s3 rb s3://{r['resource_id']} --force"
                if terminate else
                f"aws s3api put-bucket-lifecycle-configuration --bucket {r['resource_id']} --lifecycle-configuration file://glacier-lifecycle.json"
            ),
        })
    rows.sort(key=lambda x: x["days_since_access"], reverse=True)
    total_size   = round(sum(r["size_gb"] for r in rows), 1)
    total_cost   = round(sum(r["monthly_cost_usd"] for r in rows), 2)
    total_saving = round(sum(r["potential_saving"] for r in rows), 2)
    tier_summary = {}
    for r in rows:
        tier_summary.setdefault(r["access_tier"], {"count": 0, "size_gb": 0.0, "cost": 0.0})
        tier_summary[r["access_tier"]]["count"]   += 1
        tier_summary[r["access_tier"]]["size_gb"] += r["size_gb"]
        tier_summary[r["access_tier"]]["cost"]    += r["monthly_cost_usd"]
    for t in tier_summary.values():
        t["size_gb"] = round(t["size_gb"], 1)
        t["cost"]    = round(t["cost"], 2)
    return {
        "generated_at":        datetime.today().strftime("%Y-%m-%d %H:%M"),
        "total_buckets":       len(rows),
        "total_size_gb":       total_size,
        "total_monthly_cost":  total_cost,
        "potential_saving":    total_saving,
        "terminate_candidates": sum(1 for r in rows if r["terminate_candidate"]),
        "tier_summary":        tier_summary,
        "buckets":             rows,
    }


# ─── Main ─────────────────────────────────────────────────────────────────────

def run_detection(filepath="aws_cost_data.csv"):
    print("Loading data...")
    df = load_data(filepath)

    print("Running detection rules...")
    all_findings = (
        detect_idle_ec2(df) +
        detect_idle_rds(df) +
        detect_unattached_ebs(df) +
        detect_unassociated_eips(df) +
        detect_cold_s3(df) +
        detect_s3_infrequent_access(df) +
        detect_rightsizing(df)
    )

    print(f"Total findings: {len(all_findings)}")

    # Build and persist dedicated S3 analysis dataset
    s3_analysis = _build_s3_analysis(df)
    with open("s3_analysis.json", "w") as f:
        json.dump(s3_analysis, f, indent=2)

    top10 = score_and_rank(all_findings)
    summary = build_summary(all_findings, top10)

    output = {
        "summary": summary,
        "top_findings": top10,
        "all_findings": all_findings
    }

    with open("findings.json", "w") as f:
        json.dump(output, f, indent=2)

    print("\n" + "="*55)
    print(f"  CLOUD COST WASTE HUNTER — DETECTION RESULTS")
    print("="*55)
    print(f"  Total findings    : {summary['total_findings']}")
    print(f"  Monthly waste     : ${summary['total_monthly_waste']:,.2f}")
    print(f"  Annual waste      : ${summary['total_annual_waste']:,.2f}")
    print("="*55)
    print(f"\n  TOP 10 FINDINGS (ranked by waste score)\n")
    for f in top10:
        print(f"  #{f['rank']} [{f['severity']:6}] {f['category']:<22} ${f['monthly_waste_usd']:>8.2f}/mo  |  {f['resource_name']}")
    print("\n  Waste by category:")
    for cat, amt in summary["waste_by_category"].items():
        print(f"    {cat:<25} ${amt:,.2f}/mo")
    print("\n  findings.json written.")
    return output

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        description="Cloud Cost Waste Hunter — detect AWS waste from a cost CSV file"
    )
    parser.add_argument(
        "filepath",
        nargs="?",
        default="aws_cost_data.csv",
        help="Path to the AWS cost CSV file (default: aws_cost_data.csv)",
    )
    args = parser.parse_args()
    run_detection(args.filepath)
