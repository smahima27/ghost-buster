# 👻 Ghost Busters — Cloud Cost Waste Hunter

> AI-powered AWS cloud cost waste detection tool  
> Built for **Perforce Global Jam 2026**

---

## What it does

Ghost Busters scans your AWS resource data and automatically identifies wasted spend across 15 detection categories. It combines rule-based detection with Claude AI analysis to produce a prioritised, plain-English report — plus an interactive Streamlit dashboard with an embedded FinOps AI chatbot.

**Current results on sample data: 77 findings · $7,686/mo · $92,238/yr in recoverable waste**

---

## Architecture

```
aws_cost_data.csv
       │
       ▼
detection_engine.py   ←── 15 rule-based detectors
       │
       ▼
findings.json         ←── structured findings (77 items)
       │
       ▼
llm_analyzer.py       ←── Claude AI plain-English analysis
       │
       ▼
llm_report.json       ←── AI-enriched report
       │
       ▼
dashboard_AI.py       ←── Streamlit dashboard + AI chatbot
```

---

## Quick start

### 1. Clone the repo

```bash
git clone https://github.com/smahima27/ghost-buster.git
cd ghost-buster
```

### 2. Install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install streamlit plotly pandas requests
```

### 3. Run the detection engine

```bash
python3 detection_engine.py
```

Output: `findings.json` with all flagged resources ranked by waste score.

### 4. Run the AI analyser (requires Anthropic API key)

```bash
export ANTHROPIC_API_KEY='sk-ant-...'
python3 llm_analyzer.py
```

Output: `llm_report.json` with plain-English explanations and business impact.

### 5. Launch the dashboard

```bash
export ANTHROPIC_API_KEY='sk-ant-...'
streamlit run dashboard_AI.py
```

Opens at http://localhost:8501

---

## Download the sample CSV

The sample dataset (`aws_cost_data.csv`) is included in the repo with **97 simulated AWS resources** across EC2, RDS, EBS, S3, NAT Gateway, ALB/NLB, ElastiCache, Redshift, CloudWatch Logs, and Elastic IPs.

**Option 1 — via Git (recommended):**
```bash
git clone https://github.com/smahima27/ghost-buster.git
# CSV is at ghost-buster/aws_cost_data.csv
```

**Option 2 — direct download (raw file):**
```
https://raw.githubusercontent.com/smahima27/ghost-buster/feature/new-detectors/aws_cost_data.csv
```

**Option 3 — GitHub UI:**
1. Go to https://github.com/smahima27/ghost-buster
2. Click `aws_cost_data.csv`
3. Click **Download raw file** (top right)

---

## CSV schema

| Column | Type | Description |
|--------|------|-------------|
| `resource_id` | string | AWS resource ID (e.g. `i-0abc123`) |
| `resource_name` | string | Human-readable name |
| `service` | string | AWS service (EC2, RDS, EBS, S3, etc.) |
| `resource_type` | string | Instance type or volume type |
| `region` | string | AWS region |
| `team` | string | Owning team |
| `environment` | string | dev / staging / prod / sandbox |
| `cpu_avg_7d` | float | 7-day average CPU % (or traffic GB/week for NAT GW; request count for ALB/NLB) |
| `memory_avg_7d` | float | 7-day average memory % (or target group count for ALB/NLB) |
| `daily_cost_usd` | float | Daily cost in USD |
| `monthly_cost_usd` | float | Monthly cost in USD |
| `days_running` | int | Days the resource has been running |
| `last_accessed` | date | Last access date (YYYY-MM-DD) |
| `status` | string | running / stopped / unattached / orphaned / etc. |
| `tags` | string | Key:value tag pairs |

---

## Detection categories

| # | Detector | Trigger condition | Category |
|---|----------|------------------|----------|
| 1 | Idle EC2 | CPU < 5% for 7+ days | Idle Resource |
| 2 | Idle RDS | CPU < 5% for 7+ days | Idle Resource |
| 3 | Unattached EBS | Status contains "unattached" | Zombie Resource |
| 4 | Unassociated EIP | Service = Elastic IP | Zombie Resource |
| 5 | Cold S3 | Not accessed in 60+ days | Storage Optimisation |
| 6 | Rightsizing | CPU 5–20%, known instance type map | Rightsizing |
| 7 | Idle NAT Gateway | Traffic < 1 GB/week | Zombie Resource |
| 8 | Idle ALB/NLB | 0 target groups or 0 requests | Zombie Resource |
| 9 | Old-gen instances | t2/m4/c4/r4 families | Old Generation |
| 10 | Orphaned snapshots | EBS snapshot > 90 days, no source volume | Zombie Resource |
| 11 | gp2 → gp3 migration | EBS volume type starts with "gp2" | Storage Optimisation |
| 12 | On-demand no RI/SP | Running 30+ days without Reserved Instance/Savings Plan | RI/SP Optimisation |
| 13 | Infinite log retention | CloudWatch log group with no expiry | Log Retention |
| 14 | Stopped EC2 with EBS | Status = "stopped", paying for attached volumes | Zombie Resource |
| 15 | Underutilised cache/DW | ElastiCache or Redshift CPU < 10% | Idle Resource |

---

## Dashboard features

- **Metric cards** — monthly/annual opportunity, finding count
- **AI executive summary** — Claude-generated plain-English overview
- **Cost by service bar chart** + **opportunity by category donut chart**
- **Quick wins** — top 3 actionable items
- **Filterable findings** — by category and severity with remediation CLI toggle
- **FinOps AI chatbot** — ask anything about your AWS costs (powered by Claude)
- **Slack webhook** — fire a top-finding alert to any Slack channel

---

## Project structure

```
ghost-buster/
├── aws_cost_data.csv      # Sample AWS resource data (97 rows)
├── detection_engine.py    # 15 rule-based waste detectors
├── llm_analyzer.py        # Claude AI report generator
├── dashboard.py           # Basic Streamlit dashboard
├── dashboard_AI.py        # Enhanced dashboard with AI chatbot
├── findings.json          # Output of detection_engine.py
├── llm_report.json        # Output of llm_analyzer.py
└── README.md
```

---

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes (for AI steps) | Anthropic API key for Claude |

**Never commit API keys to source control.** Use `export ANTHROPIC_API_KEY='sk-ant-...'` in your shell before running.

---

## Team

Built by **Team Ghost Busters** for Perforce Global Jam 2026.
