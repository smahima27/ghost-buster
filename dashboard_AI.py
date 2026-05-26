import json, os, urllib.request, glob
import streamlit as st
import plotly.express as px
import pandas as pd
import requests

st.set_page_config(page_title="Cloud Cost Waste Hunter", page_icon="",
    layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
.main-header{font-size:1.8rem;font-weight:700;color:var(--text-color)}
.sub-header{color:#64748B;font-size:0.88rem;margin-top:-6px;margin-bottom:16px}
.metric-card{background:white;border-radius:12px;padding:16px 20px;border:1px solid #e8e8e8;box-shadow:0 1px 4px rgba(0,0,0,0.05)}
.metric-label{font-size:0.72rem;color:#888;font-weight:500;text-transform:uppercase;letter-spacing:.05em;margin-bottom:3px}
.metric-value{font-size:1.7rem;font-weight:700;color:#1a1a2e;line-height:1.1}
.metric-sub{font-size:0.78rem;color:#e05252;margin-top:3px;font-weight:500}
.exec-summary{background:#f0f7ff;border-left:4px solid #3b82f6;border-radius:0 8px 8px 0;padding:14px 18px;font-size:0.88rem;color:#1e3a5f;line-height:1.6;margin-bottom:20px}
.finding-card{background:white;border-radius:10px;padding:14px 18px;border:1px solid #e8e8e8;margin-bottom:10px;border-left:4px solid #e05252}
.finding-card.medium{border-left-color:#f59e0b}
.finding-card.low{border-left-color:#6b7280}
.finding-rank{font-size:0.72rem;color:#888;font-weight:600}
.finding-name{font-size:0.95rem;font-weight:600;color:#1a1a2e;margin:2px 0}
.finding-plain{font-size:0.84rem;color:#444;line-height:1.5;margin:5px 0}
.finding-meta{display:flex;gap:10px;flex-wrap:wrap;margin-top:6px}
.badge{font-size:0.7rem;font-weight:600;padding:2px 8px;border-radius:99px;display:inline-block}
.badge-high{background:#fee2e2;color:#b91c1c}
.badge-medium{background:#fef3c7;color:#92400e}
.badge-low{background:#f3f4f6;color:#374151}
.badge-category{background:#ede9fe;color:#5b21b6}
.saving-tag{font-size:0.84rem;font-weight:700;color:#e05252}
.cli-box{background:#1e1e2e;color:#a6e3a1;font-family:monospace;font-size:0.76rem;padding:8px 12px;border-radius:6px;margin-top:6px;overflow-x:auto;white-space:nowrap}
.quick-win{background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:10px 14px;margin-bottom:6px;font-size:0.84rem;color:#166534}
.source-badge{background:#f1f5f9;border:1px solid #e2e8f0;border-radius:6px;padding:4px 10px;font-size:0.78rem;color:#475569;margin-bottom:12px;display:inline-block}
.chat-panel{background:white;border:1px solid #e2e8f0;border-radius:12px;padding:14px;height:100%;display:flex;flex-direction:column}
.chat-panel-header{font-size:0.95rem;font-weight:600;color:#1a1a2e;margin-bottom:4px;display:flex;align-items:center;gap:8px}
.chat-panel-sub{font-size:0.76rem;color:#888;margin-bottom:12px}
.chat-bubble-user{background:#EFF6FF;border-radius:10px;padding:8px 12px;margin:4px 0;font-size:0.84rem;color:#1E3A5F}
.chat-bubble-ai{background:#F8FAFC;border:1px solid #E2E8F0;border-radius:10px;padding:8px 12px;margin:4px 0;font-size:0.84rem;color:#1E293B}
.sug-btn{font-size:0.75rem}
.untagged-banner{background:#fff7ed;border-left:4px solid #f59e0b;border-radius:0 8px 8px 0;padding:12px 16px;font-size:0.86rem;color:#92400e;margin-bottom:16px}
.untagged-row{background:white;border-radius:8px;padding:10px 14px;border:1px solid #fde68a;margin-bottom:6px;display:flex;justify-content:space-between;align-items:center}
.untagged-id{font-family:monospace;font-size:0.8rem;color:#1a1a2e;font-weight:600}
.untagged-cost{font-size:0.84rem;font-weight:700;color:#e05252}
.untagged-svc{font-size:0.72rem;background:#fef3c7;color:#92400e;padding:2px 8px;border-radius:99px;font-weight:600}
.tag-cli{background:#1e1e2e;color:#fbbf24;font-family:monospace;font-size:0.74rem;padding:6px 10px;border-radius:6px;margin-top:4px;overflow-x:auto;white-space:nowrap}
</style>
""", unsafe_allow_html=True)

# ── Load report ────────────────────────────────────────────────────────────────
@st.cache_data
def load_report(path="llm_report.json"):
    with open(path) as f:
        return json.load(f)

report     = load_report()
quick_wins = report.get("quick_wins", [])
raw_findings = report.get("findings", [])

def normalise(f):
    return {
        "rank":            f.get("rank", 0),
        "name":            f.get("service", f.get("resource_name", "Unknown")),
        "category":        f.get("category", f.get("flag", "—")),
        "plain_english":   f.get("plain_english", ""),
        "business_impact": f.get("business_impact", ""),
        "monthly_saving":  f.get("monthly_opportunity", f.get("monthly_saving", 0.0)),
        "priority_action": f.get("priority_action", ""),
        "aws_action":      f.get("aws_action", f.get("cli_fix", "")),
        "severity":        f.get("severity", "HIGH" if f.get("monthly_opportunity", f.get("monthly_saving", 0)) > 100 else "MEDIUM"),
    }

findings      = [normalise(f) for f in raw_findings]
total_monthly = report.get("total_monthly_opportunity", report.get("total_monthly_waste", 0))
total_annual  = report.get("total_annual_waste", total_monthly * 12)
total_spend   = report.get("total_monthly_spend", 0)
raw_services  = report.get("raw_data", {}).get("services", [])
all_f_legacy  = report.get("all_findings", [])

# ── Claude chatbot helpers ─────────────────────────────────────────────────────
def build_context():
    lines = [
        "You are a senior FinOps engineer assistant in the Ghost Busters Cloud Cost Waste Hunter dashboard.",
        "Answer clearly and concisely, grounding every response in the actual account data below.",
        "Keep answers to 3-5 sentences unless the user asks for detail.",
        "",
        f"Data source: {report.get('source', 'AWS Cost Explorer')}",
        f"Monthly spend: ${total_spend:,.2f}" if total_spend else "",
        f"Monthly opportunity: ${total_monthly:,.2f}",
        f"Executive summary: {report.get('executive_summary', '')}",
        "",
        "FINDINGS:",
    ]
    for fi in raw_findings:
        lines.append(
            f"#{fi.get('rank','')} {fi.get('service', fi.get('resource_name',''))} | "
            f"${fi.get('monthly_opportunity', fi.get('monthly_saving', 0)):,.2f}/mo | "
            f"{fi.get('plain_english','')[:120]} | "
            f"Action: {fi.get('priority_action','')[:80]}"
        )
    lines += ["", "QUICK WINS:"] + [f"- {w}" for w in quick_wins]
    sb = report.get("service_breakdown", {})
    if sb:
        lines += [
            f"Biggest concern: {sb.get('biggest_concern','')}",
            f"Watch list: {', '.join(sb.get('watch_list',[]))}",
        ]
    lines.append(f"Recommendation: {report.get('closing_recommendation','')}")
    return "\n".join(l for l in lines if l is not None)

def call_claude(messages):
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return "⚠️ ANTHROPIC_API_KEY not set. Run `export ANTHROPIC_API_KEY='sk-ant-...'` then restart Streamlit."
    try:
        payload = json.dumps({
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 800,
            "system": build_context(),
            "messages": messages
        }).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={"Content-Type":"application/json",
                     "x-api-key":api_key,
                     "anthropic-version":"2023-06-01"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        return data["content"][0]["text"]
    except Exception as e:
        return f"❌ Error: {e}"

if "chat_history" not in st.session_state:
    st.session_state.chat_history = []

# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## Ghost Busters")
    st.markdown("*Cloud Cost Waste Hunter*")
    st.markdown("---")
    categories   = sorted(set(f["category"] for f in findings))
    selected_cats = st.multiselect("Filter by category", categories, default=categories)
    selected_sev  = st.multiselect("Filter by severity", ["HIGH","MEDIUM","LOW"], default=["HIGH","MEDIUM","LOW"])
    st.markdown("---")
    st.markdown("**Slack webhook alert**")
    slack_url = st.text_input("Webhook URL", placeholder="https://hooks.slack.com/...")
    if st.button("🔔 Fire top finding alert", use_container_width=True):
        if slack_url and findings:
            top = findings[0]
            payload = {"blocks":[
                {"type":"header","text":{"type":"plain_text","text":" Cloud Cost Waste Hunter Alert"}},
                {"type":"section","text":{"type":"mrkdwn","text":f"*#{top['rank']} — {top['name']}*\n{top['plain_english']}"}},
                {"type":"section","fields":[
                    {"type":"mrkdwn","text":f"*Opportunity*\n${top['monthly_saving']:,.2f}/mo"},
                    {"type":"mrkdwn","text":f"*Action*\n{top['priority_action'][:80]}..."}
                ]},
                {"type":"section","text":{"type":"mrkdwn","text":f"*Total opportunity:* ${total_monthly:,.2f}/mo"}}
            ]}
            try:
                r = requests.post(slack_url, json=payload, timeout=5)
                st.success("✅ Sent!") if r.status_code==200 else st.error(f"Failed: {r.status_code}")
            except Exception as e:
                st.error(str(e))
        else:
            st.warning("Enter a Slack webhook URL first")
    st.markdown("---")
    st.caption(f"Generated: {report.get('generated_at','—')}")
    if report.get("source"): st.caption(f"Source: {report['source']}")

# ── Page header ───────────────────────────────────────────────────────────────
st.markdown('<div class="main-header"> Cloud Cost Waste Hunter</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-header">AI-powered AWS cost analysis · Perforce Global Jam 2026</div>', unsafe_allow_html=True)
if report.get("source"):
    st.markdown(f'<span class="source-badge">📊 {report["source"]}</span>', unsafe_allow_html=True)

aws_tab, gcp_tab = st.tabs(["☁️ AWS — Sample Account", "🌐 GCP — Perforce ($359K/mo)"])

with aws_tab:
    # ── MAIN LAYOUT: left 62% content | right 38% chatbot ─────────────────────────
    main_col, chat_col = st.columns([0.62, 0.38])

    with main_col:
        # Metric cards
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.markdown(f"""<div class="metric-card">
                <div class="metric-label">Monthly opportunity</div>
                <div class="metric-value">${total_monthly:,.0f}</div>
                <div class="metric-sub">recoverable now</div>
            </div>""", unsafe_allow_html=True)
        with c2:
            st.markdown(f"""<div class="metric-card">
                <div class="metric-label">Annual opportunity</div>
                <div class="metric-value">${total_annual:,.0f}</div>
                <div class="metric-sub">if unaddressed</div>
            </div>""", unsafe_allow_html=True)
        with c3:
            st.markdown(f"""<div class="metric-card">
                <div class="metric-label">Findings</div>
                <div class="metric-value">{len(findings)}</div>
                <div class="metric-sub">services flagged</div>
            </div>""", unsafe_allow_html=True)
        with c4:
            if total_spend > 0:
                pct = round((total_monthly / total_spend) * 100, 1)
                st.markdown(f"""<div class="metric-card">
                    <div class="metric-label">Total spend</div>
                    <div class="metric-value">${total_spend:,.0f}</div>
                    <div class="metric-sub">{pct}% recoverable</div>
                </div>""", unsafe_allow_html=True)
            else:
                top_f = findings[0] if findings else {}
                st.markdown(f"""<div class="metric-card">
                    <div class="metric-label">Top finding</div>
                    <div class="metric-value">{top_f.get('name','—')[:12]}</div>
                    <div class="metric-sub">${top_f.get('monthly_saving',0):,.0f}/mo</div>
                </div>""", unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        # AI summary
        st.markdown(f'<div class="exec-summary">🤖 <strong>AI Summary</strong><br>{report["executive_summary"]}</div>',
            unsafe_allow_html=True)

        # Charts
        chart_l, chart_r = st.columns(2)
        with chart_l:
            st.markdown("#### Cost by service")
            src = raw_services or []
            if src:
                svc_df = pd.DataFrame([
                    {"Service": s["service"][:22], "April ($)": s["apr_2026"]}
                    for s in sorted(src, key=lambda x: -x["apr_2026"])[:8]
                ])
                fig = px.bar(svc_df, x="April ($)", y="Service", orientation="h",
                    color="April ($)", color_continuous_scale=["#fde8e8","#e05252"], text="April ($)")
                fig.update_traces(texttemplate="$%{text:,.0f}", textposition="outside")
                fig.update_layout(showlegend=False, coloraxis_showscale=False,
                    plot_bgcolor="white", paper_bgcolor="white",
                    margin=dict(l=0,r=60,t=10,b=0), height=260,
                    yaxis=dict(showgrid=False), xaxis=dict(showgrid=True,gridcolor="#f0f0f0"))
                st.plotly_chart(fig, use_container_width=True)
            elif all_f_legacy:
                svc_t = {}
                for f in all_f_legacy:
                    svc_t[f.get("service","Other")] = svc_t.get(f.get("service","Other"),0)+f.get("monthly_waste_usd",0)
                sdf = pd.DataFrame([{"Service":k,"Waste ($)":round(v,2)} for k,v in sorted(svc_t.items(),key=lambda x:-x[1])])
                fig = px.bar(sdf,x="Waste ($)",y="Service",orientation="h",
                    color="Waste ($)",color_continuous_scale=["#fde8e8","#e05252"],text="Waste ($)")
                fig.update_traces(texttemplate="$%{text:,.0f}",textposition="outside")
                fig.update_layout(showlegend=False,coloraxis_showscale=False,
                    plot_bgcolor="white",paper_bgcolor="white",
                    margin=dict(l=0,r=60,t=10,b=0),height=260,
                    yaxis=dict(showgrid=False),xaxis=dict(showgrid=True,gridcolor="#f0f0f0"))
                st.plotly_chart(fig, use_container_width=True)

        with chart_r:
            st.markdown("#### Opportunity by category")
            cat_t = {}
            for f in findings:
                cat_t[f["category"]] = cat_t.get(f["category"],0) + f["monthly_saving"]
            if cat_t:
                cdf = pd.DataFrame([{"Category":k,"Opp ($)":round(v,2)} for k,v in sorted(cat_t.items(),key=lambda x:-x[1]) if v>0])
                fig2 = px.pie(cdf,values="Opp ($)",names="Category",
                    color_discrete_sequence=["#e05252","#f59e0b","#3b82f6","#8b5cf6","#10b981"],hole=0.45)
                fig2.update_traces(textposition="outside",textinfo="label+percent")
                fig2.update_layout(showlegend=False,paper_bgcolor="white",
                    margin=dict(l=0,r=0,t=10,b=0),height=260)
                st.plotly_chart(fig2, use_container_width=True)

        # Quick wins
        if quick_wins:
            st.markdown("#### ⚡ Quick wins")
            for w in quick_wins[:3]:
                st.markdown(f'<div class="quick-win">✅ {w}</div>', unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        # Findings
        st.markdown("#### 🔍 Flagged services")
        filtered = [f for f in findings if f["category"] in selected_cats and f["severity"] in selected_sev]
        if not filtered:
            st.info("No findings match filters.")
        else:
            show_action = st.toggle("Show AWS remediation actions", value=False)
            for f in filtered:
                sev = f["severity"].lower()
                action_html = f'<div class="cli-box">$ {f["aws_action"]}</div>' if show_action and f["aws_action"] else ""
                saving = f"${f['monthly_saving']:,.2f}/mo opportunity" if f["monthly_saving"] > 0 else "Investigate"
                st.markdown(f"""
                <div class="finding-card {sev}">
                    <div class="finding-rank">FINDING #{f['rank']}</div>
                    <div class="finding-name">{f['name']}</div>
                    <div class="finding-plain">{f['plain_english']}</div>
                    <div style="font-size:0.8rem;color:#666;margin:4px 0"><em>Impact: {f['business_impact']}</em></div>
                    <div class="finding-meta">
                        <span class="badge badge-{sev}">{f['severity']}</span>
                        <span class="badge badge-category">🏷 {f['category']}</span>
                        <span class="saving-tag">💰 {saving}</span>
                    </div>
                    <div style="font-size:0.8rem;color:#555;margin-top:6px">🔧 {f['priority_action']}</div>
                    {action_html}
                </div>""", unsafe_allow_html=True)

        # Service insights
        sb = report.get("service_breakdown", {})
        if sb:
            st.markdown("---")
            st.markdown("#### 📊 Service insights")
            si1, si2 = st.columns(2)
            with si1:
                if sb.get("biggest_concern"): st.error(f"🚨 **Biggest concern:** {sb['biggest_concern']}")
                if sb.get("most_improved"):   st.success(f"✅ **Most improved:** {sb['most_improved']}")
            with si2:
                if sb.get("watch_list"):      st.warning(f"👀 **Watch list:** {', '.join(sb['watch_list'])}")

        st.markdown("---")
        st.markdown("#### 📋 Leadership recommendation")
        st.info(report.get("closing_recommendation", ""))

        # ── Untagged Resources Panel ───────────────────────────────────────────────
        st.markdown("---")
        st.markdown("#### 🏷️ Untagged Resources")

        @st.cache_data
        def load_resource_csv():
            """Load converted_costs.csv if available, else fall back to aws_cost_data.csv."""
            for candidate in ["converted_costs.csv", "aws_cost_data.csv"]:
                path = os.environ.get("GHOSTBUSTERS_CSV", candidate)
                if os.path.exists(path):
                    try:
                        return pd.read_csv(path)
                    except Exception:
                        continue
            return pd.DataFrame()

        rdf = load_resource_csv()

        if rdf.empty:
            st.info("No resource CSV loaded. Run the detection pipeline first.")
        else:
            # Identify untagged: team is 'untagged', missing, or environment is 'unknown'
            def is_untagged(row):
                team = str(row.get("team", "")).strip().lower()
                env  = str(row.get("environment", "")).strip().lower()
                tags = str(row.get("tags", "")).strip().lower()
                return (
                    team in ("untagged", "", "nan", "none") or
                    env  in ("unknown", "", "nan", "none") or
                    tags in ("source:cost-explorer", "", "nan", "none")
                )

            untagged_df = rdf[rdf.apply(is_untagged, axis=1)].copy()
            tagged_df   = rdf[~rdf.apply(is_untagged, axis=1)].copy()

            total_resources  = len(rdf)
            untagged_count   = len(untagged_df)
            untagged_cost    = untagged_df["monthly_cost_usd"].sum() if "monthly_cost_usd" in untagged_df.columns else 0
            total_cost       = rdf["monthly_cost_usd"].sum() if "monthly_cost_usd" in rdf.columns else 0
            untagged_pct     = round(untagged_count / total_resources * 100, 1) if total_resources else 0
            untagged_cost_pct= round(untagged_cost / total_cost * 100, 1) if total_cost else 0

            # Metric cards row
            ut1, ut2, ut3, ut4 = st.columns(4)
            with ut1:
                st.markdown(f"""<div class="metric-card">
                    <div class="metric-label">Untagged resources</div>
                    <div class="metric-value" style="color:#f59e0b">{untagged_count}</div>
                    <div class="metric-sub" style="color:#f59e0b">{untagged_pct}% of total</div>
                </div>""", unsafe_allow_html=True)
            with ut2:
                st.markdown(f"""<div class="metric-card">
                    <div class="metric-label">Untagged monthly spend</div>
                    <div class="metric-value" style="color:#e05252">${untagged_cost:,.0f}</div>
                    <div class="metric-sub">{untagged_cost_pct}% of total spend</div>
                </div>""", unsafe_allow_html=True)
            with ut3:
                st.markdown(f"""<div class="metric-card">
                    <div class="metric-label">Tagged resources</div>
                    <div class="metric-value" style="color:#10b981">{len(tagged_df)}</div>
                    <div class="metric-sub">{100-untagged_pct}% coverage</div>
                </div>""", unsafe_allow_html=True)
            with ut4:
                st.markdown(f"""<div class="metric-card">
                    <div class="metric-label">Untagged annual cost</div>
                    <div class="metric-value" style="color:#e05252">${untagged_cost*12:,.0f}</div>
                    <div class="metric-sub">no ownership visibility</div>
                </div>""", unsafe_allow_html=True)

            st.markdown("<br>", unsafe_allow_html=True)

            if untagged_count == 0:
                st.success("✅ All resources are tagged. Great governance!")
            else:
                st.markdown(
                    f'<div class="untagged-banner">⚠️ <strong>{untagged_count} resources ({untagged_pct}%) have no team/environment tags</strong> — '
                    f'accounting for <strong>${untagged_cost:,.2f}/mo</strong> of spend with no ownership visibility. '
                    f'Without tags you cannot chargeback costs, enforce policies, or identify owners when issues arise.</div>',
                    unsafe_allow_html=True
                )

                # Donut: tagged vs untagged by cost
                ut_chart_l, ut_chart_r = st.columns(2)
                with ut_chart_l:
                    st.markdown("**Tagging coverage by spend**")
                    tag_pie = pd.DataFrame([
                        {"Status": "Untagged", "Cost": round(untagged_cost, 2)},
                        {"Status": "Tagged",   "Cost": round(total_cost - untagged_cost, 2)},
                    ])
                    fig_tag = px.pie(tag_pie, values="Cost", names="Status",
                        color_discrete_map={"Untagged": "#f59e0b", "Tagged": "#10b981"}, hole=0.5)
                    fig_tag.update_traces(textposition="outside", textinfo="label+percent")
                    fig_tag.update_layout(showlegend=False, paper_bgcolor="white",
                        margin=dict(l=0,r=0,t=10,b=0), height=220)
                    st.plotly_chart(fig_tag, use_container_width=True)

                with ut_chart_r:
                    st.markdown("**Untagged spend by service**")
                    if "service" in untagged_df.columns:
                        svc_untagged = (
                            untagged_df.groupby("service")["monthly_cost_usd"]
                            .sum().reset_index()
                            .sort_values("monthly_cost_usd", ascending=True)
                            .tail(8)
                        )
                        svc_untagged.columns = ["Service", "Cost"]
                        fig_svc = px.bar(svc_untagged, x="Cost", y="Service", orientation="h",
                            color="Cost", color_continuous_scale=["#fef3c7", "#f59e0b"], text="Cost")
                        fig_svc.update_traces(texttemplate="$%{text:,.0f}", textposition="outside")
                        fig_svc.update_layout(showlegend=False, coloraxis_showscale=False,
                            plot_bgcolor="white", paper_bgcolor="white",
                            margin=dict(l=0,r=60,t=10,b=0), height=220,
                            yaxis=dict(showgrid=False), xaxis=dict(showgrid=True, gridcolor="#f0f0f0"))
                        st.plotly_chart(fig_svc, use_container_width=True)

                # Table of untagged resources
                st.markdown("<br>", unsafe_allow_html=True)
                st.markdown("**Resources missing tags** — sorted by monthly cost")

                show_cols = [c for c in ["resource_id","resource_name","service","region","team","environment","monthly_cost_usd","tags"] if c in untagged_df.columns]
                display_df = (
                    untagged_df[show_cols]
                    .sort_values("monthly_cost_usd", ascending=False)
                    .reset_index(drop=True)
                )
                display_df.index += 1

                # Search filter
                tag_search = st.text_input("🔍 Filter by resource ID or service", placeholder="e.g. vol- or EC2", key="tag_search")
                if tag_search:
                    mask = display_df.apply(lambda row: tag_search.lower() in str(row).lower(), axis=1)
                    display_df = display_df[mask]

                st.dataframe(
                    display_df,
                    use_container_width=True,
                    height=min(400, 40 + len(display_df) * 35),
                    column_config={
                        "monthly_cost_usd": st.column_config.NumberColumn("Monthly Cost ($)", format="$%.2f"),
                        "resource_id":      st.column_config.TextColumn("Resource ID"),
                        "resource_name":    st.column_config.TextColumn("Name"),
                        "service":          st.column_config.TextColumn("Service"),
                        "region":           st.column_config.TextColumn("Region"),
                        "team":             st.column_config.TextColumn("Team"),
                        "environment":      st.column_config.TextColumn("Environment"),
                        "tags":             st.column_config.TextColumn("Tags"),
                    }
                )

                # Tagging CLI helper
                st.markdown("<br>", unsafe_allow_html=True)
                st.markdown("**Fix it — bulk tag via AWS CLI:**")
                top_untagged = untagged_df.sort_values("monthly_cost_usd", ascending=False).head(3)
                for _, row in top_untagged.iterrows():
                    rid = row.get("resource_id", "")
                    region = row.get("region", "us-east-1")
                    svc = str(row.get("service", "")).lower()
                    if "ec2" in svc or rid.startswith(("i-", "vol-", "snap-")):
                        cli = f"aws ec2 create-tags --resources {rid} --tags Key=team,Value=your-team Key=environment,Value=prod Key=owner,Value=your-name --region {region}"
                    elif "rds" in svc:
                        cli = f"aws rds add-tags-to-resource --resource-name {rid} --tags Key=team,Value=your-team Key=environment,Value=prod --region {region}"
                    elif "s3" in svc:
                        cli = f"aws s3api put-bucket-tagging --bucket {rid} --tagging 'TagSet=[{{Key=team,Value=your-team}},{{Key=environment,Value=prod}}]'"
                    else:
                        cli = f"aws resourcegroupstaggingapi tag-resources --resource-arn-list {rid} --tags team=your-team,environment=prod,owner=your-name --region {region}"
                    st.markdown(
                        f'<div class="tag-cli">$ {cli}</div>',
                        unsafe_allow_html=True
                    )

        st.markdown("---")
        st.caption("Built for Perforce Global Jam 2026 · Team Ghost Busters · Cloud Cost Waste Hunter")

    # ── RIGHT PANEL: FinOps AI Chatbot ────────────────────────────────────────────
    with chat_col:
        st.markdown("""
        <div style="background:white;border:1px solid #e2e8f0;border-radius:12px;padding:16px;position:sticky;top:0">
            <div style="font-size:1rem;font-weight:600;color:#1a1a2e;margin-bottom:2px">🤖 FinOps AI</div>
            <div style="font-size:0.76rem;color:#888;margin-bottom:12px;border-bottom:1px solid #f1f5f9;padding-bottom:10px">
                Ask anything about your AWS costs
            </div>
        </div>
        """, unsafe_allow_html=True)

        # Suggested questions
        suggestions = [
            "Which service should I fix first?",
            "Why did EC2-Other spike?",
            "How much can we save on Neptune?",
            "What is the DevOpsAgent charge?",
            "Give me a 3-step action plan",
        ]
        st.markdown("<p style='font-size:0.76rem;color:#888;margin:10px 0 6px'>💡 Suggested questions:</p>",
            unsafe_allow_html=True)
        for i, sug in enumerate(suggestions):
            if st.button(sug, key=f"sug_{i}", use_container_width=True):
                st.session_state.chat_history.append({"role":"user","content":sug})
                with st.spinner("Thinking..."):
                    ans = call_claude(st.session_state.chat_history)
                st.session_state.chat_history.append({"role":"assistant","content":ans})

        st.markdown("<div style='margin-top:10px'>", unsafe_allow_html=True)

        # Chat history
        for msg in st.session_state.chat_history:
            if msg["role"] == "user":
                st.markdown(
                    f"<div class='chat-bubble-user'><strong>You:</strong> {msg['content']}</div>",
                    unsafe_allow_html=True)
            else:
                st.markdown(
                    f"<div class='chat-bubble-ai'><strong>🤖 FinOps AI:</strong> {msg['content']}</div>",
                    unsafe_allow_html=True)

        st.markdown("</div>", unsafe_allow_html=True)

        # Input
        if prompt_input := st.chat_input("Ask about your AWS costs..."):
            st.session_state.chat_history.append({"role":"user","content":prompt_input})
            with st.spinner("Thinking..."):
                ans = call_claude(st.session_state.chat_history)
            st.session_state.chat_history.append({"role":"assistant","content":ans})
            st.rerun()

        if st.session_state.chat_history:
            if st.button("🗑️ Clear chat", use_container_width=True):
                st.session_state.chat_history = []
                st.rerun()

with gcp_tab:
    # ── GCP LAYOUT ────────────────────────────────────────────────────────────────────
    gcp_main, gcp_chat = st.columns([0.62, 0.38])

    with gcp_main:
        _gcp_report_path = os.environ.get("GHOSTBUSTERS_GCP_REPORT", "gcp_report.json")

        @st.cache_data
        def load_gcp_report(path=_gcp_report_path):
            if not os.path.exists(path):
                return None
            with open(path) as _f:
                return json.load(_f)

        gr = load_gcp_report()

        if gr is None:
            st.info("No GCP report found. Run the pipeline:")
            st.code("python3 gcp_detection_engine.py\nexport ANTHROPIC_API_KEY=$(cat SM_api_key) && python3 gcp_analyzer.py", language="bash")
        else:
            g_spend   = gr.get("total_monthly_spend", 0)
            g_opp     = gr.get("total_monthly_opportunity", 0)
            g_annual  = gr.get("total_annual_opportunity", g_opp * 12)
            g_saved   = gr.get("savings_already_applied", gr.get("savings_efficiency_pct", 0))
            g_findings= gr.get("findings", [])
            g_list    = gr.get("total_list_cost", 0)
            g_eff     = gr.get("savings_efficiency_pct", 0)

            # Source badge
            if gr.get("source"):
                st.markdown(f'''<span class="source-badge">📊 {gr["source"]}</span>''', unsafe_allow_html=True)

            # Metric cards
            gc1, gc2, gc3, gc4 = st.columns(4)
            with gc1:
                st.markdown(f'''<div class="metric-card">
                    <div class="metric-label">GCP Monthly Spend</div>
                    <div class="metric-value">${g_spend:,.0f}</div>
                    <div class="metric-sub">after SADA discounts</div>
                </div>''', unsafe_allow_html=True)
            with gc2:
                st.markdown(f'''<div class="metric-card">
                    <div class="metric-label">SADA Savings Applied</div>
                    <div class="metric-value" style="color:#10b981">{g_eff:.0f}%</div>
                    <div class="metric-sub">${g_list - g_spend:,.0f} saved/mo</div>
                </div>''', unsafe_allow_html=True)
            with gc3:
                st.markdown(f'''<div class="metric-card">
                    <div class="metric-label">Additional Opportunity</div>
                    <div class="metric-value" style="color:#e05252">${g_opp:,.0f}</div>
                    <div class="metric-sub">additional/mo</div>
                </div>''', unsafe_allow_html=True)
            with gc4:
                st.markdown(f'''<div class="metric-card">
                    <div class="metric-label">Annual Opportunity</div>
                    <div class="metric-value" style="color:#e05252">${g_annual:,.0f}</div>
                    <div class="metric-sub">if unaddressed</div>
                </div>''', unsafe_allow_html=True)

            st.markdown("<br>", unsafe_allow_html=True)

            # AI Summary
            st.markdown(
                f'''<div class="exec-summary">🤖 <strong>AI Summary</strong><br>{gr.get("executive_summary","")}</div>''',
                unsafe_allow_html=True)

            # Charts row
            gc_l, gc_r = st.columns(2)
            with gc_l:
                st.markdown("#### GCP spend by service")
                svcs = gr.get("raw_services", [])
                if svcs:
                    svc_df = pd.DataFrame([
                        {"Service": s["service"][:28], "Monthly ($)": round(s["subtotal"], 2)}
                        for s in sorted(svcs, key=lambda x: -x["subtotal"])[:10]
                    ])
                    fig_g = px.bar(svc_df, x="Monthly ($)", y="Service", orientation="h",
                        color="Monthly ($)", color_continuous_scale=["#e0f2fe","#0ea5e9"], text="Monthly ($)")
                    fig_g.update_traces(texttemplate="$%{text:,.0f}", textposition="outside")
                    fig_g.update_layout(showlegend=False, coloraxis_showscale=False,
                        plot_bgcolor="white", paper_bgcolor="white",
                        margin=dict(l=0,r=70,t=10,b=0), height=300,
                        yaxis=dict(showgrid=False), xaxis=dict(showgrid=True, gridcolor="#f0f0f0"))
                    st.plotly_chart(fig_g, use_container_width=True)

            with gc_r:
                st.markdown("#### List cost vs actual spend")
                svcs = gr.get("raw_services", [])
                if svcs:
                    top5 = sorted(svcs, key=lambda x: -x["subtotal"])[:6]
                    savings_df = pd.DataFrame([
                        {"Service": s["service"][:20], "Type": "Actual (after savings)", "Cost": round(s["subtotal"], 2)}
                        for s in top5
                    ] + [
                        {"Service": s["service"][:20], "Type": "SADA Savings", "Cost": round(s.get("total_savings", 0), 2)}
                        for s in top5
                    ])
                    fig_s = px.bar(savings_df, x="Cost", y="Service", color="Type", orientation="h",
                        color_discrete_map={"Actual (after savings)": "#0ea5e9", "SADA Savings": "#10b981"},
                        barmode="stack")
                    fig_s.update_layout(showlegend=True, plot_bgcolor="white", paper_bgcolor="white",
                        margin=dict(l=0,r=70,t=10,b=0), height=300,
                        yaxis=dict(showgrid=False), xaxis=dict(showgrid=True, gridcolor="#f0f0f0"),
                        legend=dict(orientation="h", yanchor="bottom", y=1.02))
                    st.plotly_chart(fig_s, use_container_width=True)

            # MoM trend chart
            svcs = gr.get("raw_services", [])
            trend_svcs = [s for s in svcs if s.get("pct_change") is not None and abs(s["pct_change"]) > 5 and s["subtotal"] > 100]
            if trend_svcs:
                st.markdown("#### Month-over-month spend changes")
                trend_df = pd.DataFrame([
                    {"Service": s["service"][:28], "Change (%)": s["pct_change"]}
                    for s in sorted(trend_svcs, key=lambda x: x["pct_change"])
                ])
                colors = ["#ef4444" if v > 0 else "#10b981" for v in trend_df["Change (%)"]]
                fig_t = px.bar(trend_df, x="Change (%)", y="Service", orientation="h",
                    color="Change (%)",
                    color_continuous_scale=[[0,"#10b981"],[0.5,"#f59e0b"],[1,"#ef4444"]],
                    text="Change (%)")
                fig_t.update_traces(texttemplate="%{text:+.0f}%", textposition="outside")
                fig_t.add_vline(x=0, line_width=1, line_color="#94a3b8")
                fig_t.update_layout(showlegend=False, coloraxis_showscale=False,
                    plot_bgcolor="white", paper_bgcolor="white",
                    margin=dict(l=0,r=70,t=10,b=0), height=max(220, len(trend_df)*32),
                    yaxis=dict(showgrid=False), xaxis=dict(showgrid=True, gridcolor="#f0f0f0"))
                st.plotly_chart(fig_t, use_container_width=True)

            # AI Spend insight
            ai_insight = gr.get("ai_spend_insight", "")
            if ai_insight:
                st.markdown("---")
                st.markdown("#### 🤖 AI / LLM Spend")
                st.markdown(f'''<div class="exec-summary" style="border-color:#8b5cf6">{ai_insight}</div>''', unsafe_allow_html=True)
                # AI services breakdown
                ai_svcs = [s for s in gr.get("raw_services",[]) if any(kw in s["service"].lower() for kw in ["claude","gemini","vertex","dialogflow"])]
                if ai_svcs:
                    ai_total = sum(s["subtotal"] for s in ai_svcs)
                    ai_cols = st.columns(len(ai_svcs[:4]))
                    for col, svc in zip(ai_cols, sorted(ai_svcs, key=lambda x: -x["subtotal"])[:4]):
                        pct = svc.get("pct_change")
                        pct_str = f"{pct:+.0f}% MoM" if pct is not None else "New"
                        with col:
                            st.markdown(f'''<div class="metric-card">
                                <div class="metric-label">{svc["service"][:22]}</div>
                                <div class="metric-value" style="color:#8b5cf6;font-size:1.3rem">${svc["subtotal"]:,.0f}</div>
                                <div class="metric-sub" style="color:#7c3aed">{pct_str}</div>
                            </div>''', unsafe_allow_html=True)

            # Quick wins
            qw = gr.get("quick_wins", [])
            if qw:
                st.markdown("---")
                st.markdown("#### ⚡ Quick wins")
                for w in qw[:3]:
                    st.markdown(f'''<div class="quick-win">✅ {w}</div>''', unsafe_allow_html=True)

            st.markdown("<br>", unsafe_allow_html=True)

            # Findings
            st.markdown("#### 🔍 GCP findings")
            if not g_findings:
                st.info("No findings in report.")
            else:
                show_gcp_actions = st.toggle("Show GCP remediation actions", value=False, key="gcp_actions_toggle")
                for f in g_findings:
                    sev = f.get("severity","MEDIUM").lower()
                    opp = f.get("monthly_opportunity", 0)
                    saving_str = f"${opp:,.2f}/mo opportunity" if opp > 0 else "Investigate"
                    gcp_action = f.get("gcp_action", "")
                    action_html = f'''<div class="cli-box">$ {gcp_action}</div>''' if show_gcp_actions and gcp_action else ""
                    st.markdown(f"""
                    <div class="finding-card {sev}">
                        <div class="finding-rank">FINDING #{f.get("rank","")}</div>
                        <div class="finding-name">{f.get("service","")}</div>
                        <div class="finding-plain">{f.get("plain_english","")}</div>
                        <div style="font-size:0.8rem;color:#666;margin:4px 0"><em>Impact: {f.get("business_impact","")}</em></div>
                        <div class="finding-meta">
                            <span class="badge badge-{sev}">{f.get("severity","MEDIUM")}</span>
                            <span class="badge badge-category">🏷 {f.get("category","")}</span>
                            <span class="saving-tag">💰 {saving_str}</span>
                        </div>
                        <div style="font-size:0.8rem;color:#555;margin-top:6px">🔧 {f.get("priority_action","")}</div>
                        {action_html}
                    </div>""", unsafe_allow_html=True)

            # SADA savings assessment + leadership recommendation
            sada_insight = gr.get("sada_savings_assessment","")
            if sada_insight:
                st.markdown("---")
                st.markdown("#### 💰 SADA Discount Assessment")
                st.success(sada_insight)

            st.markdown("---")
            st.markdown("#### 📋 Leadership recommendation")
            st.info(gr.get("closing_recommendation",""))

            st.markdown("---")
            st.caption("Built for Perforce Global Jam 2026 · Team Ghost Busters · Cloud Cost Waste Hunter")

    with gcp_chat:
        st.markdown("""
        <div style="background:white;border:1px solid #e2e8f0;border-radius:12px;padding:16px;position:sticky;top:0">
            <div style="font-size:1rem;font-weight:600;color:#1a1a2e;margin-bottom:2px">🤖 GCP FinOps AI</div>
            <div style="font-size:0.76rem;color:#888;margin-bottom:12px;border-bottom:1px solid #f1f5f9;padding-bottom:10px">
                Ask anything about your GCP costs
            </div>
        </div>
        """, unsafe_allow_html=True)

        if gr is None:
            st.info("Run the GCP pipeline to enable this chatbot.")
        else:
            def build_gcp_context():
                lines = [
                    "You are a senior FinOps engineer assistant for GCP cloud costs at Perforce.",
                    "Answer clearly and concisely, grounding every answer in the actual billing data below.",
                    "Keep answers to 3-5 sentences unless the user asks for detail.",
                    "",
                    f"Data source: {gr.get('source','SADA GCP Billing Report')}",
                    f"Total monthly GCP spend: ${gr.get('total_monthly_spend',0):,.2f}",
                    f"Additional monthly opportunity: ${gr.get('total_monthly_opportunity',0):,.2f}",
                    f"Executive summary: {gr.get('executive_summary','')}",
                    "",
                    "FINDINGS:",
                ]
                for fi in g_findings:
                    lines.append(
                        f"#{fi.get('rank','')} {fi.get('service','')} | "
                        f"${fi.get('monthly_opportunity',0):,.2f}/mo | "
                        f"{fi.get('plain_english','')[:120]}"
                    )
                lines += ["", "QUICK WINS:"] + [f"- {w}" for w in gr.get("quick_wins",[])]
                lines.append(f"AI insight: {gr.get('ai_spend_insight','')}")
                return "\n".join(l for l in lines if l is not None)

            def call_claude_gcp(messages):
                api_key = os.environ.get("ANTHROPIC_API_KEY","")
                if not api_key:
                    return "⚠️ ANTHROPIC_API_KEY not set."
                try:
                    payload = json.dumps({
                        "model": "claude-sonnet-4-20250514",
                        "max_tokens": 800,
                        "system": build_gcp_context(),
                        "messages": messages
                    }).encode()
                    req = urllib.request.Request(
                        "https://api.anthropic.com/v1/messages",
                        data=payload,
                        headers={"Content-Type":"application/json","x-api-key":api_key,"anthropic-version":"2023-06-01"},
                        method="POST"
                    )
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        data = json.loads(resp.read().decode())
                    return data["content"][0]["text"]
                except Exception as e:
                    return f"❌ Error: {e}"

            if "gcp_chat_history" not in st.session_state:
                st.session_state.gcp_chat_history = []

            gcp_suggestions = [
                "What is driving our GCP spend?",
                "Why is Claude Sonnet so expensive?",
                "How much are we saving with SADA?",
                "What can we do about Compute Engine costs?",
                "Give me a GCP cost reduction plan",
            ]
            st.markdown("<p style='font-size:0.76rem;color:#888;margin:10px 0 6px'>💡 Suggested questions:</p>", unsafe_allow_html=True)
            for i, sug in enumerate(gcp_suggestions):
                if st.button(sug, key=f"gcp_sug_{i}", use_container_width=True):
                    st.session_state.gcp_chat_history.append({"role":"user","content":sug})
                    with st.spinner("Thinking..."):
                        ans = call_claude_gcp(st.session_state.gcp_chat_history)
                    st.session_state.gcp_chat_history.append({"role":"assistant","content":ans})

            st.markdown("<div style='margin-top:10px'>", unsafe_allow_html=True)
            for msg in st.session_state.gcp_chat_history:
                if msg["role"] == "user":
                    st.markdown(f"<div class='chat-bubble-user'><strong>You:</strong> {msg['content']}</div>", unsafe_allow_html=True)
                else:
                    st.markdown(f"<div class='chat-bubble-ai'><strong>🤖 GCP AI:</strong> {msg['content']}</div>", unsafe_allow_html=True)
            st.markdown("</div>", unsafe_allow_html=True)

            if prompt_gcp := st.chat_input("Ask about your GCP costs...", key="gcp_chat_input"):
                st.session_state.gcp_chat_history.append({"role":"user","content":prompt_gcp})
                with st.spinner("Thinking..."):
                    ans = call_claude_gcp(st.session_state.gcp_chat_history)
                st.session_state.gcp_chat_history.append({"role":"assistant","content":ans})
                st.rerun()

            if st.session_state.gcp_chat_history:
                if st.button("🗑️ Clear GCP chat", use_container_width=True, key="clear_gcp_chat"):
                    st.session_state.gcp_chat_history = []
                    st.rerun()
