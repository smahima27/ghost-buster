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

# Threshold for NAT Gateway idle detection
NAT_IDLE_TRAFFIC_GB = 1.0   # GB/week — below this = idle NAT Gateway
SNAPSHOT_AGE_THRESHOLD  = 90    # days — orphaned snapshots older than this = waste
GP2_TO_GP3_SAVING_PCT   = 0.20  # gp3 is 20% cheaper than gp2 per GB
RI_SP_DAYS_THRESHOLD    = 30    # days on-demand before flagging for RI/SP purchase
RI_SP_SAVING_PCT        = 0.40  # estimated saving with Reserved Instance or Savings Plan
CACHE_CPU_THRESHOLD     = 10.0  # % — ElastiCache/Redshift below this = underutilized
LOG_RETENTION_SAVING_PCT = 0.70 # saving from applying 30-day retention to infinite log groups

# Old-generation → new-generation map: "old_type": ("new_type", old_hourly_usd, new_hourly_usd)
OLD_GEN_MAP = {
    "t2.micro":    ("t3.micro",    0.0116, 0.0104),
    "t2.small":    ("t3.small",    0.023,  0.0208),
    "t2.medium":   ("t3.medium",   0.0464, 0.0416),
    "t2.large":    ("t3.large",    0.0928, 0.0832),
    "t2.xlarge":   ("t3.xlarge",   0.1856, 0.1664),
    "t2.2xlarge":  ("t3.2xlarge",  0.3712, 0.3328),
    "m4.large":    ("m5.large",    0.1,    0.096),
    "m4.xlarge":   ("m5.xlarge",   0.2,    0.192),
    "m4.2xlarge":  ("m5.2xlarge",  0.4,    0.384),
    "m4.4xlarge":  ("m5.4xlarge",  0.8,    0.768),
    "c4.large":    ("c5.large",    0.1,    0.085),
    "c4.xlarge":   ("c5.xlarge",   0.199,  0.17),
    "c4.2xlarge":  ("c5.2xlarge",  0.398,  0.34),
    "c4.4xlarge":  ("c5.4xlarge",  0.796,  0.68),
    "r4.large":    ("r5.large",    0.133,  0.126),
    "r4.xlarge":   ("r5.xlarge",   0.266,  0.252),
    "r4.2xlarge":  ("r5.2xlarge",  0.532,  0.504),
    "r4.4xlarge":  ("r5.4xlarge",  1.064,  1.008),
}

# ─── Load data ────────────────────────────────────────────────────────────────
def load_data(filepath="aws_cost_data.csv"):
    df = pd.read_csv(filepath, parse_dates=["last_accessed"])
    df["days_since_access"] = (datetime.today() - df["last_accessed"]).dt.days
    return df

# ─── Detection rules ──────────────────────────────────────────────────────────

def detect_idle_ec2(df):
    findings = []
    ec2 = df[df["service"] == "EC2"].copy()
    idle = ec2[
        (ec2["cpu_avg_7d"] < IDLE_CPU_THRESHOLD) &
        (ec2["days_running"] >= IDLE_DAYS_THRESHOLD) &
        (~ec2["status"].str.contains("stopped", case=False, na=False))
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
        findings.append({
            "finding_id":       f"S3-COLD-{r['resource_id'][-8:]}",
            "category":         "Storage Optimisation",
            "severity":         "MEDIUM",
            "service":          "S3",
            "resource_id":      r["resource_id"],
            "resource_name":    r["resource_name"],
            "region":           r["region"],
            "team":             r["team"],
            "environment":      r["environment"],
            "detail":           f"Bucket not accessed in {r['days_since_access']} days but on S3 Standard pricing. Move to Glacier.",
            "monthly_waste_usd": saving,
            "recommendation":   "Apply S3 Intelligent-Tiering or Lifecycle rule to move to Glacier after 30 days.",
            "cli_fix":          f"aws s3api put-bucket-lifecycle-configuration --bucket {r['resource_id']} --lifecycle-configuration file://glacier-lifecycle.json"
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


def detect_nat_idle(df):
    # For NAT Gateway rows, cpu_avg_7d encodes network traffic in GB/week
    findings = []
    nats = df[
        (df["service"] == "NAT Gateway") &
        (df["cpu_avg_7d"] < NAT_IDLE_TRAFFIC_GB)
    ]
    for _, r in nats.iterrows():
        traffic_gb = float(r["cpu_avg_7d"])
        findings.append({
            "finding_id":        f"NAT-IDLE-{r['resource_id'][-6:]}",
            "category":          "Zombie Resource",
            "severity":          "MEDIUM",
            "service":           "NAT Gateway",
            "resource_id":       r["resource_id"],
            "resource_name":     r["resource_name"],
            "region":            r["region"],
            "team":              r["team"],
            "environment":       r["environment"],
            "detail":            f"NAT Gateway processed only {traffic_gb:.2f} GB in the past 7 days. AWS charges a $32/mo minimum regardless of traffic volume.",
            "monthly_waste_usd": float(r["monthly_cost_usd"]),
            "recommendation":    "Delete idle NAT Gateway. Verify no workloads depend on it for outbound internet access before removal.",
            "cli_fix":           f"aws ec2 delete-nat-gateway --nat-gateway-id {r['resource_id']} --region {r['region']}"
        })
    return findings


def detect_idle_load_balancers(df):
    # For ALB/NLB rows: cpu_avg_7d = request_count_7d, memory_avg_7d = target_group_count
    findings = []
    lbs = df[
        (df["service"].isin(["ALB", "NLB"])) &
        ((df["memory_avg_7d"] == 0) | (df["cpu_avg_7d"] == 0))
    ]
    for _, r in lbs.iterrows():
        tg_count  = int(r["memory_avg_7d"])
        req_count = int(r["cpu_avg_7d"])
        reason = "no registered target groups" if tg_count == 0 else "zero requests in the past 7 days"
        findings.append({
            "finding_id":        f"LB-IDLE-{r['resource_id'][-6:]}",
            "category":          "Zombie Resource",
            "severity":          "MEDIUM",
            "service":           r["service"],
            "resource_id":       r["resource_id"],
            "resource_name":     r["resource_name"],
            "region":            r["region"],
            "team":              r["team"],
            "environment":       r["environment"],
            "detail":            f"{r['service']} has {reason}. Load balancers cost $16+/mo even with zero traffic.",
            "monthly_waste_usd": float(r["monthly_cost_usd"]),
            "recommendation":    "Delete the load balancer and clean up any associated DNS records or ACM certificates.",
            "cli_fix":           f"aws elbv2 delete-load-balancer --load-balancer-arn {r['resource_id']} --region {r['region']}"
        })
    return findings


def detect_old_gen_instances(df):
    findings = []
    ec2 = df[df["service"] == "EC2"]
    for _, r in ec2.iterrows():
        itype = r["resource_type"]
        if itype not in OLD_GEN_MAP:
            continue
        new_type, old_hourly, new_hourly = OLD_GEN_MAP[itype]
        saving = round((old_hourly - new_hourly) * 24 * 30, 2)
        if saving <= 0:
            continue
        pct = round((old_hourly - new_hourly) / old_hourly * 100)
        findings.append({
            "finding_id":        f"OLDGEN-{r['resource_id'][-6:]}",
            "category":          "Old Generation",
            "severity":          "LOW",
            "service":           "EC2",
            "resource_id":       r["resource_id"],
            "resource_name":     r["resource_name"],
            "region":            r["region"],
            "team":              r["team"],
            "environment":       r["environment"],
            "detail":            f"Running deprecated {itype}. Upgrading to {new_type} saves ${saving}/mo ({pct}% cheaper) with better CPU performance and no architectural changes.",
            "monthly_waste_usd": saving,
            "recommendation":    f"Stop instance, change type to {new_type}, restart. Schedule during next maintenance window.",
            "cli_fix":           f"aws ec2 stop-instances --instance-ids {r['resource_id']} --region {r['region']} && aws ec2 modify-instance-attribute --instance-id {r['resource_id']} --instance-type {{Value={new_type}}} --region {r['region']}"
        })
    return findings


def detect_orphan_snapshots(df):
    findings = []
    snaps = df[
        (df["service"] == "EBS Snapshot") &
        (df["status"].str.contains("orphaned", case=False, na=False)) &
        (df["days_running"] >= SNAPSHOT_AGE_THRESHOLD)
    ]
    for _, r in snaps.iterrows():
        findings.append({
            "finding_id":        f"SNAP-ORPHAN-{r['resource_id'][-6:]}",
            "category":          "Zombie Resource",
            "severity":          "MEDIUM" if r["monthly_cost_usd"] > 30 else "LOW",
            "service":           "EBS Snapshot",
            "resource_id":       r["resource_id"],
            "resource_name":     r["resource_name"],
            "region":            r["region"],
            "team":              r["team"],
            "environment":       r["environment"],
            "detail":            f"Orphaned snapshot {r['days_running']} days old — source volume no longer exists. Accruing ${r['monthly_cost_usd']}/mo in S3 snapshot storage.",
            "monthly_waste_usd": float(r["monthly_cost_usd"]),
            "recommendation":    "Delete orphaned snapshot after confirming the data is no longer needed for recovery.",
            "cli_fix":           f"aws ec2 delete-snapshot --snapshot-id {r['resource_id']} --region {r['region']}"
        })
    return findings


def detect_gp2_volumes(df):
    findings = []
    gp2 = df[
        (df["service"] == "EBS") &
        (df["resource_type"].str.startswith("gp2", na=False))
    ]
    for _, r in gp2.iterrows():
        saving = round(float(r["monthly_cost_usd"]) * GP2_TO_GP3_SAVING_PCT, 2)
        findings.append({
            "finding_id":        f"GP2-VOLUME-{r['resource_id'][-6:]}",
            "category":          "Storage Optimisation",
            "severity":          "LOW",
            "service":           "EBS",
            "resource_id":       r["resource_id"],
            "resource_name":     r["resource_name"],
            "region":            r["region"],
            "team":              r["team"],
            "environment":       r["environment"],
            "detail":            f"gp2 volume costs ${r['monthly_cost_usd']}/mo. Migrating to gp3 saves 20% (${saving}/mo) and delivers 3x baseline IOPS with 125 MB/s throughput at no extra cost.",
            "monthly_waste_usd": saving,
            "recommendation":    "Modify volume type from gp2 to gp3. Zero downtime — change takes effect within minutes.",
            "cli_fix":           f"aws ec2 modify-volume --volume-id {r['resource_id']} --volume-type gp3 --region {r['region']}"
        })
    return findings


def detect_ondemand_no_coverage(df):
    # status "running-ondemand" flags EC2 instances confirmed without RI/SP coverage
    findings = []
    ondemand = df[
        (df["service"] == "EC2") &
        (df["status"].str.contains("ondemand", case=False, na=False)) &
        (df["days_running"] >= RI_SP_DAYS_THRESHOLD)
    ]
    for _, r in ondemand.iterrows():
        saving = round(float(r["monthly_cost_usd"]) * RI_SP_SAVING_PCT, 2)
        findings.append({
            "finding_id":        f"RI-MISSING-{r['resource_id'][-6:]}",
            "category":          "RI/SP Optimisation",
            "severity":          "MEDIUM" if r["monthly_cost_usd"] > 200 else "LOW",
            "service":           "EC2",
            "resource_id":       r["resource_id"],
            "resource_name":     r["resource_name"],
            "region":            r["region"],
            "team":              r["team"],
            "environment":       r["environment"],
            "detail":            f"Instance has run on-demand for {r['days_running']} days with no Reserved Instance or Savings Plan. On-demand is 30-60% more expensive than committed pricing.",
            "monthly_waste_usd": saving,
            "recommendation":    "Purchase a 1-year Compute Savings Plan or Reserved Instance. Break-even in ~7 months vs on-demand pricing.",
            "cli_fix":           f"aws ce get-reservation-purchase-recommendation --service 'Amazon EC2' --region {r['region']}"
        })
    return findings


def detect_infinite_log_retention(df):
    findings = []
    logs = df[
        (df["service"] == "CloudWatch Logs") &
        (df["resource_type"].str.contains("infinite", case=False, na=False))
    ]
    for _, r in logs.iterrows():
        saving = round(float(r["monthly_cost_usd"]) * LOG_RETENTION_SAVING_PCT, 2)
        findings.append({
            "finding_id":        f"LOG-INFINITE-{r['resource_id'][-7:]}",
            "category":          "Log Retention",
            "severity":          "LOW",
            "service":           "CloudWatch Logs",
            "resource_id":       r["resource_id"],
            "resource_name":     r["resource_name"],
            "region":            r["region"],
            "team":              r["team"],
            "environment":       r["environment"],
            "detail":            f"Log group has infinite retention — logs never expire and accumulate at ${r['monthly_cost_usd']}/mo. Setting 30-day retention reduces storage cost by ~70%.",
            "monthly_waste_usd": saving,
            "recommendation":    "Set retention policy to 30, 60, or 90 days depending on compliance requirements. Zero downtime.",
            "cli_fix":           f"aws logs put-retention-policy --log-group-name \"{r['resource_name']}\" --retention-in-days 30 --region {r['region']}"
        })
    return findings


def detect_stopped_ec2_with_ebs(df):
    findings = []
    stopped = df[
        (df["service"] == "EC2") &
        (df["status"].str.contains("^stopped$", case=False, na=False, regex=True))
    ]
    for _, r in stopped.iterrows():
        findings.append({
            "finding_id":        f"STOPPED-EC2-{r['resource_id'][-6:]}",
            "category":          "Zombie Resource",
            "severity":          "MEDIUM" if r["monthly_cost_usd"] > 20 else "LOW",
            "service":           "EC2",
            "resource_id":       r["resource_id"],
            "resource_name":     r["resource_name"],
            "region":            r["region"],
            "team":              r["team"],
            "environment":       r["environment"],
            "detail":            f"Instance stopped for {r['days_running']} days but attached EBS volumes still incur ${r['monthly_cost_usd']}/mo in storage charges with zero utilization.",
            "monthly_waste_usd": float(r["monthly_cost_usd"]),
            "recommendation":    "Snapshot and terminate the instance, or detach and delete unused EBS volumes to eliminate storage costs.",
            "cli_fix":           f"aws ec2 create-image --instance-id {r['resource_id']} --name 'backup-before-terminate' --region {r['region']} && aws ec2 terminate-instances --instance-ids {r['resource_id']} --region {r['region']}"
        })
    return findings


def detect_underutilized_cache_redshift(df):
    findings = []
    cache = df[
        (df["service"].isin(["ElastiCache", "Redshift"])) &
        (df["cpu_avg_7d"] < CACHE_CPU_THRESHOLD) &
        (df["days_running"] >= IDLE_DAYS_THRESHOLD)
    ]
    for _, r in cache.iterrows():
        if r["service"] == "ElastiCache":
            cli = f"aws elasticache delete-cache-cluster --cache-cluster-id {r['resource_id']} --region {r['region']}"
        else:
            cli = f"aws redshift delete-cluster --cluster-identifier {r['resource_id']} --skip-final-cluster-snapshot --region {r['region']}"
        findings.append({
            "finding_id":        f"IDLE-{r['service'].upper()[:5]}-{r['resource_id'][-6:]}",
            "category":          "Idle Resource",
            "severity":          "HIGH" if r["monthly_cost_usd"] > 100 else "MEDIUM",
            "service":           r["service"],
            "resource_id":       r["resource_id"],
            "resource_name":     r["resource_name"],
            "region":            r["region"],
            "team":              r["team"],
            "environment":       r["environment"],
            "detail":            f"{r['service']} running at only {r['cpu_avg_7d']}% CPU for {r['days_running']} days — well below {CACHE_CPU_THRESHOLD}% utilization threshold.",
            "monthly_waste_usd": float(r["monthly_cost_usd"]),
            "recommendation":    f"Delete or downsize. For ElastiCache consider Serverless (scales to zero). For Redshift use pause/resume scheduling.",
            "cli_fix":           cli
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
        detect_rightsizing(df) +
        detect_nat_idle(df) +
        detect_idle_load_balancers(df) +
        detect_old_gen_instances(df) +
        detect_orphan_snapshots(df) +
        detect_gp2_volumes(df) +
        detect_ondemand_no_coverage(df) +
        detect_infinite_log_retention(df) +
        detect_stopped_ec2_with_ebs(df) +
        detect_underutilized_cache_redshift(df)
    )

    print(f"Total findings: {len(all_findings)}")

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
    import os
    csv_path = os.environ.get("GHOSTBUSTERS_CSV", "aws_cost_data.csv")
    run_detection(csv_path)
