import io
import zipfile
from pathlib import Path

import pandas as pd
import streamlit as st

from content_fetcher import fetch_post
from rewriter import analyze_post, rewrite_post
from sitemap_crawler import get_site_pages

import anthropic

# ---------------------------------------------------------------------------
# GSC CSV parser
# ---------------------------------------------------------------------------

_GSC_URL_COLUMNS = ["Top pages", "URL", "url", "Page", "page", "Address", "address"]


def parse_gsc_csv(uploaded_file) -> tuple[list[str], str | None]:
    try:
        df = pd.read_csv(uploaded_file)
    except Exception as e:
        return [], f"Could not read CSV: {e}"

    url_col = None
    for candidate in _GSC_URL_COLUMNS:
        if candidate in df.columns:
            url_col = candidate
            break

    if url_col is None:
        for col in df.columns:
            sample = df[col].dropna().astype(str).head(5)
            if sample.str.startswith("http").sum() >= 3:
                url_col = col
                break

    if url_col is None:
        cols = ", ".join(df.columns.tolist())
        return [], f"Could not find a URL column. Columns found: {cols}"

    urls = (
        df[url_col]
        .dropna()
        .astype(str)
        .str.strip()
        .pipe(lambda s: s[s.str.startswith("http")])
        .tolist()
    )

    if not urls:
        return [], f"Column '{url_col}' found but contained no URLs starting with http."

    return urls, None


# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Blog Revival Agent",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------------------------
# Design system injection
# ---------------------------------------------------------------------------

st.markdown("""
<link href="https://fonts.googleapis.com/css2?family=Syne:wght@400;600;700;800&family=JetBrains+Mono:wght@300;400;500&family=Instrument+Sans:ital,wght@0,400;0,500;0,600;1,400&display=swap" rel="stylesheet">

<style>
/* ── Reset & Base ─────────────────────────────────────────────────────── */
:root {
  --bg:        #080b10;
  --surface:   #0d1117;
  --card:      #111720;
  --border:    #1c2730;
  --border-hi: #243040;
  --tx:        #dce8f0;
  --tx-2:      #6b8394;
  --tx-3:      #334455;
  --green:     #3dffa0;
  --green-dim: #0d3325;
  --green-glow:rgba(61,255,160,0.15);
  --amber:     #f5c842;
  --red:       #ff4f6a;
  --font-disp: 'Syne', sans-serif;
  --font-mono: 'JetBrains Mono', monospace;
  --font-body: 'Instrument Sans', sans-serif;
}

/* Hide Streamlit chrome */
#MainMenu, footer, header { visibility: hidden; }
[data-testid="stDecoration"] { display: none; }

/* App background */
.stApp {
  background-color: var(--bg) !important;
  font-family: var(--font-body) !important;
}

.block-container {
  padding: 2.5rem 3rem 4rem 3rem !important;
  max-width: 1200px !important;
}

/* ── Typography overrides ─────────────────────────────────────────────── */
h1, h2, h3, h4 {
  font-family: var(--font-disp) !important;
  color: var(--tx) !important;
  letter-spacing: -0.02em !important;
}

p, li, label, .stMarkdown {
  font-family: var(--font-body) !important;
  color: var(--tx-2) !important;
}

/* ── Form inputs ──────────────────────────────────────────────────────── */
.stTextInput label,
.stTextArea label,
.stFileUploader label {
  font-family: var(--font-mono) !important;
  font-size: 0.72rem !important;
  font-weight: 500 !important;
  letter-spacing: 0.1em !important;
  text-transform: uppercase !important;
  color: var(--tx-2) !important;
}

.stTextInput input,
.stTextArea textarea {
  background-color: var(--card) !important;
  border: 1px solid var(--border) !important;
  border-radius: 6px !important;
  color: var(--tx) !important;
  font-family: var(--font-mono) !important;
  font-size: 0.85rem !important;
  caret-color: var(--green) !important;
  transition: border-color 0.15s ease !important;
}

.stTextInput input:focus,
.stTextArea textarea:focus {
  border-color: var(--green) !important;
  box-shadow: 0 0 0 2px var(--green-glow) !important;
  outline: none !important;
}

.stTextInput input::placeholder,
.stTextArea textarea::placeholder {
  color: var(--tx-3) !important;
}

/* ── File uploader ────────────────────────────────────────────────────── */
[data-testid="stFileUploader"] {
  background-color: var(--card) !important;
  border: 1px dashed var(--border-hi) !important;
  border-radius: 6px !important;
  padding: 0.5rem !important;
  transition: border-color 0.15s ease !important;
}

[data-testid="stFileUploader"]:hover {
  border-color: var(--green) !important;
}

[data-testid="stFileUploadDropzone"] {
  background: transparent !important;
  border: none !important;
}

[data-testid="stFileUploadDropzone"] p,
[data-testid="stFileUploadDropzone"] span {
  color: var(--tx-2) !important;
  font-family: var(--font-body) !important;
}

/* ── Buttons ──────────────────────────────────────────────────────────── */
.stButton button,
.stFormSubmitButton button,
.stDownloadButton button {
  font-family: var(--font-mono) !important;
  font-size: 0.78rem !important;
  font-weight: 500 !important;
  letter-spacing: 0.08em !important;
  text-transform: uppercase !important;
  border-radius: 5px !important;
  transition: all 0.15s ease !important;
  cursor: pointer !important;
}

/* Primary button (submit) */
.stFormSubmitButton button[kind="primaryFormSubmit"],
.stFormSubmitButton button {
  background: var(--green) !important;
  color: #030808 !important;
  border: none !important;
  padding: 0.65rem 1.5rem !important;
}

.stFormSubmitButton button:hover {
  background: #5fffb8 !important;
  box-shadow: 0 0 20px var(--green-glow) !important;
  transform: translateY(-1px) !important;
}

/* Secondary / download buttons */
.stDownloadButton button {
  background: transparent !important;
  color: var(--green) !important;
  border: 1px solid var(--green) !important;
}

.stDownloadButton button:hover {
  background: var(--green-dim) !important;
  box-shadow: 0 0 12px var(--green-glow) !important;
}

/* ── Progress bar ─────────────────────────────────────────────────────── */
.stProgress > div > div {
  background-color: var(--border) !important;
  border-radius: 3px !important;
}

.stProgress > div > div > div {
  background: linear-gradient(90deg, var(--green), #5fffb8) !important;
  border-radius: 3px !important;
  box-shadow: 0 0 10px var(--green-glow) !important;
}

[data-testid="stProgressBar"] {
  background-color: var(--border) !important;
}

[data-testid="stProgressBar"] > div {
  background: linear-gradient(90deg, var(--green), #5fffb8) !important;
  box-shadow: 0 0 10px var(--green-glow) !important;
}

/* ── Status widget ────────────────────────────────────────────────────── */
[data-testid="stStatus"] {
  background-color: var(--card) !important;
  border: 1px solid var(--border) !important;
  border-radius: 6px !important;
  color: var(--tx) !important;
}

[data-testid="stStatus"] summary {
  font-family: var(--font-mono) !important;
  font-size: 0.82rem !important;
  color: var(--tx) !important;
}

/* ── Expanders ────────────────────────────────────────────────────────── */
[data-testid="stExpander"] {
  background-color: var(--card) !important;
  border: 1px solid var(--border) !important;
  border-radius: 6px !important;
  margin-bottom: 0.5rem !important;
}

[data-testid="stExpander"] summary {
  font-family: var(--font-body) !important;
  font-size: 0.9rem !important;
  font-weight: 600 !important;
  color: var(--tx) !important;
  padding: 0.75rem 1rem !important;
}

[data-testid="stExpander"] summary:hover {
  color: var(--green) !important;
}

/* ── Metrics ──────────────────────────────────────────────────────────── */
[data-testid="stMetric"] {
  background-color: var(--card) !important;
  border: 1px solid var(--border) !important;
  border-radius: 6px !important;
  padding: 1rem !important;
}

[data-testid="stMetricLabel"] {
  font-family: var(--font-mono) !important;
  font-size: 0.7rem !important;
  letter-spacing: 0.08em !important;
  text-transform: uppercase !important;
  color: var(--tx-2) !important;
}

[data-testid="stMetricValue"] {
  font-family: var(--font-mono) !important;
  font-size: 1.6rem !important;
  font-weight: 400 !important;
  color: var(--tx) !important;
}

[data-testid="stMetricDelta"] svg { display: none; }

[data-testid="stMetricDelta"] {
  font-family: var(--font-mono) !important;
  font-size: 0.78rem !important;
}

/* ── Alerts ───────────────────────────────────────────────────────────── */
[data-testid="stAlert"] {
  border-radius: 5px !important;
  font-family: var(--font-body) !important;
  font-size: 0.85rem !important;
  border-left-width: 3px !important;
  padding: 0.65rem 1rem !important;
}

.stSuccess {
  background-color: rgba(61,255,160,0.07) !important;
  border-left-color: var(--green) !important;
  color: var(--tx) !important;
}

.stWarning {
  background-color: rgba(245,200,66,0.07) !important;
  border-left-color: var(--amber) !important;
  color: var(--tx) !important;
}

.stError {
  background-color: rgba(255,79,106,0.07) !important;
  border-left-color: var(--red) !important;
  color: var(--tx) !important;
}

.stInfo {
  background-color: rgba(100,180,255,0.07) !important;
  border-left-color: #64b4ff !important;
  color: var(--tx) !important;
}

/* ── Dataframe ────────────────────────────────────────────────────────── */
[data-testid="stDataFrame"] {
  border: 1px solid var(--border) !important;
  border-radius: 6px !important;
  overflow: hidden !important;
}

.stDataFrame iframe {
  background: var(--card) !important;
}

/* ── Form container ───────────────────────────────────────────────────── */
[data-testid="stForm"] {
  background: var(--card) !important;
  border: 1px solid var(--border) !important;
  border-radius: 8px !important;
  padding: 1.5rem !important;
}

/* ── Divider ──────────────────────────────────────────────────────────── */
hr {
  border-color: var(--border) !important;
  margin: 1.5rem 0 !important;
}

/* ── Spinner ──────────────────────────────────────────────────────────── */
[data-testid="stSpinner"] {
  color: var(--green) !important;
}

/* ── Tooltip ──────────────────────────────────────────────────────────── */
[data-testid="stTooltipIcon"] { color: var(--tx-3) !important; }

/* ── Section label helper ─────────────────────────────────────────────── */
.section-label {
  font-family: var(--font-mono);
  font-size: 0.68rem;
  font-weight: 500;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--green);
  margin-bottom: 0.3rem;
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.section-label::before {
  content: '';
  display: inline-block;
  width: 16px;
  height: 1px;
  background: var(--green);
}

/* ── Step card ────────────────────────────────────────────────────────── */
.step-header {
  display: flex;
  align-items: center;
  gap: 0.85rem;
  margin: 1.8rem 0 0.8rem 0;
}

.step-num {
  font-family: var(--font-mono);
  font-size: 0.7rem;
  font-weight: 500;
  letter-spacing: 0.1em;
  color: #030808;
  background: var(--green);
  border-radius: 3px;
  padding: 0.15rem 0.4rem;
  line-height: 1.6;
}

.step-title {
  font-family: var(--font-disp);
  font-size: 1.1rem;
  font-weight: 700;
  color: var(--tx);
  letter-spacing: -0.02em;
}

/* ── Hero ─────────────────────────────────────────────────────────────── */
.hero {
  padding: 2.5rem 0 2rem 0;
  border-bottom: 1px solid var(--border);
  margin-bottom: 2rem;
}

.hero-eyebrow {
  font-family: var(--font-mono);
  font-size: 0.7rem;
  font-weight: 400;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--green);
  margin-bottom: 0.75rem;
}

.hero-title {
  font-family: var(--font-disp);
  font-size: 2.6rem;
  font-weight: 800;
  color: var(--tx);
  letter-spacing: -0.04em;
  line-height: 1.1;
  margin-bottom: 0.8rem;
}

.hero-title span {
  color: var(--green);
}

.hero-sub {
  font-family: var(--font-body);
  font-size: 0.95rem;
  color: var(--tx-2);
  max-width: 520px;
  line-height: 1.6;
}

/* ── Stat pills ───────────────────────────────────────────────────────── */
.stat-row {
  display: flex;
  gap: 0.6rem;
  margin-top: 1.2rem;
  flex-wrap: wrap;
}

.stat-pill {
  font-family: var(--font-mono);
  font-size: 0.72rem;
  letter-spacing: 0.06em;
  color: var(--tx-2);
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 0.3rem 0.65rem;
}

.stat-pill strong {
  color: var(--tx);
  font-weight: 500;
}

/* ── Audit badge ──────────────────────────────────────────────────────── */
.verdict-badge {
  display: inline-block;
  font-family: var(--font-mono);
  font-size: 0.65rem;
  font-weight: 500;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  padding: 0.2rem 0.55rem;
  border-radius: 3px;
}

.verdict-thin   { background: rgba(255,79,106,0.15); color: #ff4f6a; border: 1px solid rgba(255,79,106,0.3); }
.verdict-average{ background: rgba(245,200,66,0.12); color: #f5c842; border: 1px solid rgba(245,200,66,0.3); }
.verdict-good   { background: rgba(61,255,160,0.12); color: #3dffa0; border: 1px solid rgba(61,255,160,0.3); }

/* ── Audit finding rows ───────────────────────────────────────────────── */
.audit-row {
  display: flex;
  align-items: flex-start;
  gap: 0.6rem;
  padding: 0.45rem 0;
  border-bottom: 1px solid var(--border);
  font-family: var(--font-body);
  font-size: 0.84rem;
  color: var(--tx-2);
}

.audit-row:last-child { border-bottom: none; }

.audit-icon {
  font-size: 0.7rem;
  padding-top: 0.15rem;
  min-width: 16px;
  color: var(--tx-3);
}

/* ── Results table label ──────────────────────────────────────────────── */
.results-heading {
  font-family: var(--font-disp);
  font-size: 1.35rem;
  font-weight: 700;
  color: var(--tx);
  letter-spacing: -0.02em;
  margin: 1.5rem 0 0.75rem 0;
}

</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Hero header
# ---------------------------------------------------------------------------

st.markdown("""
<div class="hero">
  <div class="hero-eyebrow">⚡ AI Ranking — Content Tools</div>
  <div class="hero-title">Blog Content<br><span>Revival Agent</span></div>
  <div class="hero-sub">
    Rewrite "Crawled — currently not indexed" posts into comprehensive,
    Google-ready content using the capsule content technique.
  </div>
  <div class="stat-row">
    <div class="stat-pill"><strong>2-pass</strong> Claude analysis</div>
    <div class="stat-pill"><strong>claude-sonnet-4-6</strong></div>
    <div class="stat-pill">Capsule content optimised</div>
    <div class="stat-pill">Auto sitemap discovery</div>
  </div>
</div>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

if "results" not in st.session_state:
    st.session_state["results"] = []
if "url_queue" not in st.session_state:
    st.session_state["url_queue"] = []
if "processing" not in st.session_state:
    st.session_state["processing"] = False
if "proc_domain" not in st.session_state:
    st.session_state["proc_domain"] = ""
if "proc_api_key" not in st.session_state:
    st.session_state["proc_api_key"] = ""
if "proc_site_pages" not in st.session_state:
    st.session_state["proc_site_pages"] = []
if "total_urls" not in st.session_state:
    st.session_state["total_urls"] = 0
if "total_cost_usd" not in st.session_state:
    st.session_state["total_cost_usd"] = 0.0
if "total_input_tok" not in st.session_state:
    st.session_state["total_input_tok"] = 0
if "total_output_tok" not in st.session_state:
    st.session_state["total_output_tok"] = 0

# ---------------------------------------------------------------------------
# Input form
# ---------------------------------------------------------------------------

st.markdown('<div class="section-label">Configuration</div>', unsafe_allow_html=True)

with st.form("revival_form"):
    col1, col2 = st.columns([1, 1], gap="large")

    with col1:
        domain = st.text_input(
            "Website domain",
            placeholder="https://example.com",
            help="Your site's base URL — used to discover your sitemap for internal link suggestions.",
        )
        api_key = st.text_input(
            "Anthropic API key",
            type="password",
            help="Stored in this browser session only. Never written to disk.",
        )

    with col2:
        gsc_csv = st.file_uploader(
            "GSC CSV export",
            type=["csv"],
            help="Pages report → filter 'Crawled - currently not indexed' → Export CSV.",
        )
        urls_input = st.text_area(
            "Or paste URLs manually (one per line)",
            placeholder="https://example.com/blog/post-1\nhttps://example.com/blog/post-2",
            height=108,
            help="Used only if no CSV is uploaded above.",
        )

    st.markdown("<br>", unsafe_allow_html=True)
    submitted = st.form_submit_button(
        "⚡  Run Analysis & Rewrite",
        type="primary",
        use_container_width=True,
    )


# ---------------------------------------------------------------------------
# Processing
# ---------------------------------------------------------------------------

if submitted:
    errors = []
    if not domain.strip():
        errors.append("Please enter your website domain.")
    if not api_key.strip():
        errors.append("Please enter your Anthropic API key.")
    for err in errors:
        st.error(err)
    if errors:
        st.stop()

    # Resolve URL list
    urls = []
    if gsc_csv is not None:
        urls, csv_error = parse_gsc_csv(gsc_csv)
        if csv_error:
            st.error(f"CSV parse error: {csv_error}")
            st.stop()
        st.success(f"Loaded **{len(urls)} URLs** from `{gsc_csv.name}`")
    elif urls_input.strip():
        urls = [u.strip() for u in urls_input.strip().splitlines() if u.strip()]

    if not urls:
        st.error("Please upload a GSC CSV or paste at least one URL.")
        st.stop()

    try:
        anthropic.Anthropic(api_key=api_key.strip())
    except Exception as e:
        st.error(f"Failed to initialize Anthropic client: {e}")
        st.stop()

    # ── Step 1: Sitemap ─────────────────────────────────────────────────
    st.markdown("""
    <div class="step-header">
      <span class="step-num">01</span>
      <span class="step-title">Sitemap Discovery</span>
    </div>
    """, unsafe_allow_html=True)

    with st.spinner(f"Scanning {domain} for sitemap..."):
        site_pages = get_site_pages(domain, session_state=st.session_state)

    if site_pages:
        st.success(f"Found **{len(site_pages)} pages** — internal links will be suggested automatically.")
    else:
        st.warning(
            "No sitemap found. Internal link suggestions will be limited. "
            "Make sure your sitemap is at `/sitemap.xml` or declared in `robots.txt`."
        )

    # Set up incremental processing queue
    st.session_state["results"] = []
    st.session_state["url_queue"] = list(urls)
    st.session_state["total_urls"] = len(urls)
    st.session_state["processing"] = True
    st.session_state["proc_domain"] = domain.strip()
    st.session_state["proc_api_key"] = api_key.strip()
    st.session_state["proc_site_pages"] = site_pages
    st.session_state["total_cost_usd"] = 0.0
    st.session_state["total_input_tok"] = 0
    st.session_state["total_output_tok"] = 0
    st.rerun()


# ---------------------------------------------------------------------------
# Step 2: Incremental processing (one URL per rerun — enables stop button)
# ---------------------------------------------------------------------------

if st.session_state.get("processing"):
    queue      = st.session_state["url_queue"]
    total      = st.session_state["total_urls"]
    done_count = len(st.session_state["results"])

    st.markdown(f"""
    <div class="step-header">
      <span class="step-num">02</span>
      <span class="step-title">Processing {total} Post{"s" if total != 1 else ""}</span>
    </div>
    """, unsafe_allow_html=True)

    # Stop button — detected at the start of the next rerun
    if st.button("Stop processing", type="secondary"):
        st.session_state["processing"] = False
        st.session_state["url_queue"] = []
        st.rerun()

    progress_val = done_count / total if total > 0 else 0
    current_url  = queue[0] if queue else ""
    progress_text = (
        f"Post {done_count + 1} of {total}  ·  {current_url}"
        if queue else "All posts processed."
    )
    st.progress(progress_val, text=progress_text)

    col1, col2, col3 = st.columns(3)
    col1.metric("Est. API cost",  f"${st.session_state['total_cost_usd']:.4f}")
    col2.metric("Tokens used",    f"{st.session_state['total_input_tok'] + st.session_state['total_output_tok']:,}")
    col3.metric("Posts done",     f"{done_count} / {total}")

    if queue:
        url    = queue.pop(0)
        domain = st.session_state["proc_domain"]
        client = anthropic.Anthropic(api_key=st.session_state["proc_api_key"])
        site_pages = st.session_state["proc_site_pages"]

        output_dir = Path("output")
        output_dir.mkdir(exist_ok=True)

        with st.status(f"Processing: {url}", expanded=True) as status:

            st.write("Fetching content...")
            post = fetch_post(url)

            if post.get("error"):
                st.error(f"Fetch failed: {post['error']}")
                status.update(label=f"Failed: {url}", state="error", expanded=False)
                st.session_state["results"].append({"url": url, "error": post["error"]})
            else:
                st.write(
                    f"Fetched — **{post['word_count']} words**, "
                    f"{len(post['headings'])} headings, "
                    f"{len(post['internal_links'])} internal / {len(post['external_links'])} external links"
                )

                st.write("Running SEO audit (pass 1 of 2)...")
                try:
                    audit, usage_a = analyze_post(post, site_pages, client, domain=domain)
                    st.session_state["total_cost_usd"]   += usage_a["cost_usd"]
                    st.session_state["total_input_tok"]  += usage_a["input_tokens"]
                    st.session_state["total_output_tok"] += usage_a["output_tokens"]

                    verdict     = audit.get("verdict", "unknown")
                    thin_count  = len(audit.get("thin_sections", []))
                    missing_int = len(audit.get("missing_internal_links", []))
                    st.write(f"Audit done — verdict: **{verdict}** · {thin_count} thin section(s) · {missing_int} link gap(s)")

                    st.write("Rewriting with capsule content technique (pass 2 of 2)...")
                    rewritten, usage_r = rewrite_post(post, audit, site_pages, client, domain=domain)
                    st.session_state["total_cost_usd"]   += usage_r["cost_usd"]
                    st.session_state["total_input_tok"]  += usage_r["input_tokens"]
                    st.session_state["total_output_tok"] += usage_r["output_tokens"]

                    new_word_count = len(rewritten.split())
                    output_dir.joinpath(f"{post['slug']}-rewritten.md").write_text(rewritten, encoding="utf-8")

                    internal_links_added = rewritten.count(f"]({domain.rstrip('/')}")
                    external_links_added = rewritten.count("](https://") - internal_links_added

                    st.session_state["results"].append({
                        "url": url,
                        "title": post["title"],
                        "slug": post["slug"],
                        "word_count_before": post["word_count"],
                        "word_count_after": new_word_count,
                        "internal_links_added": internal_links_added,
                        "external_links_added": external_links_added,
                        "audit": audit,
                        "rewritten": rewritten,
                        "error": None,
                    })

                    status.update(
                        label=f"Done: {post['title'] or url}  ({post['word_count']} → {new_word_count} words)",
                        state="complete",
                        expanded=False,
                    )

                except Exception as e:
                    st.error(f"Processing failed: {e}")
                    status.update(label=f"Failed: {url}", state="error", expanded=False)
                    st.session_state["results"].append({"url": url, "error": str(e)})

        st.session_state["url_queue"] = queue

        if not queue:
            st.session_state["processing"] = False

        st.rerun()

    else:
        st.session_state["processing"] = False
        st.rerun()


# ---------------------------------------------------------------------------
# Step 3: Results (rendered from session state so downloads don't reset view)
# ---------------------------------------------------------------------------

if st.session_state.get("results"):
    results = st.session_state["results"]

    st.markdown("""
    <div class="step-header">
      <span class="step-num">03</span>
      <span class="step-title">Results</span>
    </div>
    """, unsafe_allow_html=True)

    successful = [r for r in results if not r.get("error")]
    failed     = [r for r in results if r.get("error")]

    if failed:
        with st.expander(f"{len(failed)} post(s) failed to process", expanded=True):
            for r in failed:
                st.error(f"`{r['url']}` — {r['error']}")

    if not successful:
        st.warning("No posts were successfully processed.")
        st.stop()

    # Summary table
    summary_rows = []
    for r in successful:
        delta = r["word_count_after"] - r["word_count_before"]
        summary_rows.append({
            "Title":              r.get("title", r["url"]),
            "Words before":       r["word_count_before"],
            "Words after":        r["word_count_after"],
            "Δ Words":            f"+{delta}" if delta >= 0 else str(delta),
            "Internal links":     r["internal_links_added"],
            "External links":     r["external_links_added"],
            "Original verdict":   r["audit"].get("verdict", ""),
        })

    st.dataframe(pd.DataFrame(summary_rows), use_container_width=True, hide_index=True)

    # Per-post previews
    st.markdown('<div class="results-heading">Rewritten Posts</div>', unsafe_allow_html=True)

    for r in successful:
        label = r.get("title") or r["url"]
        verdict_val = r["audit"].get("verdict", "average")
        verdict_cls = f"verdict-{verdict_val}" if verdict_val in ("thin", "average", "good") else "verdict-average"

        with st.expander(label, expanded=False):
            audit = r["audit"]

            col_a, col_b = st.columns([3, 2], gap="large")

            with col_a:
                st.markdown('<div class="section-label">Audit findings</div>', unsafe_allow_html=True)

                thin = audit.get("thin_sections", [])
                outdated = audit.get("outdated_claims", [])
                missing_int_list = audit.get("missing_internal_links", [])
                missing_ext = audit.get("missing_external_links", [])

                findings_html = f'<span class="verdict-badge {verdict_cls}">{verdict_val}</span><br><br>'
                if thin:
                    findings_html += f'<div class="audit-row"><span class="audit-icon">▸</span>Thin sections ({len(thin)}): {", ".join(thin[:3])}</div>'
                if outdated:
                    findings_html += f'<div class="audit-row"><span class="audit-icon">▸</span>Outdated claims: {len(outdated)} found</div>'
                if missing_int_list:
                    findings_html += f'<div class="audit-row"><span class="audit-icon">▸</span>Internal link gaps: {len(missing_int_list)}</div>'
                if missing_ext:
                    findings_html += f'<div class="audit-row"><span class="audit-icon">▸</span>Claims needing citations: {len(missing_ext)}</div>'
                if not (thin or outdated or missing_int_list or missing_ext):
                    findings_html += '<div class="audit-row">No critical issues detected.</div>'

                st.markdown(findings_html, unsafe_allow_html=True)

            with col_b:
                st.markdown('<div class="section-label">Stats</div>', unsafe_allow_html=True)
                st.metric(
                    "Word count",
                    f"{r['word_count_after']:,}",
                    delta=r["word_count_after"] - r["word_count_before"],
                )
                st.markdown(
                    f'<div class="audit-row"><span class="audit-icon">▸</span>'
                    f'Internal links added: <strong style="color:var(--tx)">{r["internal_links_added"]}</strong></div>'
                    f'<div class="audit-row"><span class="audit-icon">▸</span>'
                    f'External links added: <strong style="color:var(--tx)">{r["external_links_added"]}</strong></div>',
                    unsafe_allow_html=True,
                )

            st.markdown("<hr>", unsafe_allow_html=True)
            st.markdown('<div class="section-label">Rewritten post</div>', unsafe_allow_html=True)
            st.markdown(r["rewritten"])

            st.download_button(
                label=f"Download  {r['slug']}-rewritten.md",
                data=r["rewritten"].encode("utf-8"),
                file_name=f"{r['slug']}-rewritten.md",
                mime="text/markdown",
                key=f"dl_{r['slug']}",
            )

    # ZIP download
    st.markdown("<hr>", unsafe_allow_html=True)
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for r in successful:
            zf.writestr(f"{r['slug']}-rewritten.md", r["rewritten"])
    zip_buffer.seek(0)

    st.download_button(
        label="⬇  Download all posts as ZIP",
        data=zip_buffer,
        file_name="rewritten-posts.zip",
        mime="application/zip",
        type="primary",
        use_container_width=True,
    )
