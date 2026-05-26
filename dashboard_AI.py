import json, os, urllib.request
import streamlit as st
import plotly.express as px
import pandas as pd
import requests

st.set_page_config(page_title="Cloud Cost Waste Hunter", page_icon="👻",
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
    st.markdown("## 👻 Ghost Busters")
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
                {"type":"header","text":{"type":"plain_text","text":"👻 Cloud Cost Waste Hunter Alert"}},
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
st.markdown('<div class="main-header">👻 Cloud Cost Waste Hunter</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-header">AI-powered AWS cost analysis · Perforce Global Jam 2026</div>', unsafe_allow_html=True)
if report.get("source"):
    st.markdown(f'<span class="source-badge">📊 {report["source"]}</span>', unsafe_allow_html=True)

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