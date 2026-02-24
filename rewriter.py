import json

import anthropic

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

ANALYSIS_PROMPT = """You are an SEO content auditor. Analyze this blog post and return ONLY a valid JSON object with no extra text, no markdown fences.

Return exactly this structure:
{{
  "thin_sections": ["list of H2 heading texts where the section content is under 150 words"],
  "outdated_claims": ["exact sentences or phrases that appear outdated, reference old stats, or use vague 'recently' language"],
  "missing_internal_links": ["topics or keywords mentioned in the post that match available site pages"],
  "missing_external_links": ["specific factual claims that need a citation - quote the claim"],
  "overall_word_count": {word_count},
  "verdict": "thin"
}}

Verdict must be one of: "thin" (under 800 words or sparse), "average" (800-1200 words), "good" (1200+ words with depth).

BLOG POST TITLE: {title}
CURRENT WORD COUNT: {word_count}

CURRENT POST CONTENT:
{body_text}

AVAILABLE SITE PAGES (for internal link matching):
{site_pages}

Return only the JSON object."""


REWRITE_PROMPT = """You are an expert SEO content writer. Rewrite the blog post below following every rule in this brief exactly.

TONE & VOICE (critical — do this first):
- Read the original post carefully and identify the author's tone: casual or formal, technical or plain-English, use of "you" or third-person, sentence length, vocabulary level, use of humour or directness
- The rewrite MUST sound like the same person wrote it. Do not homogenise or make it generic
- If the original is conversational, keep it conversational. If it's technical, keep it technical
- Preserve any recurring phrases, stylistic quirks, or structural habits the author uses
- NEVER use em dashes (—) anywhere in the post. Replace them with a colon, comma, parentheses, or split into two sentences instead

CAPSULE CONTENT STRUCTURE (apply to 60% of H2 sections):
- H2 heading must be phrased as a question
- Immediately after the H2, write a 30-60 word direct answer in **bold** - self-contained enough to be a Google featured snippet
- Follow with 2-3 supporting paragraphs (depth, examples, data)

CONTENT REQUIREMENTS:
- Update any outdated facts with accurate 2024-2025 information
- Minimum 1,200 words total
- Fix all issues listed in the audit below

EXTERNAL LINKS (important):
- Back up every factual claim with a real, working URL from a credible source
- Use well-known sources: government sites (.gov), major publications (e.g. Search Engine Journal, Moz, Ahrefs blog, Google Search Central, HubSpot, Semrush blog), Wikipedia for general facts, academic/research sources
- Format: [descriptive anchor text](https://real-url.com)
- Only use a placeholder like (SOURCE: domain.com) if you are genuinely uncertain of the exact URL — prefer real links

INTERNAL LINKS (important):
- Add at least 3-5 internal links using the site pages listed below
- Use the FULL URL provided for each page — do not shorten to a relative path
- Format: [contextual anchor text](https://full-url-from-sitemap)
- Anchor text must be natural and contextual, never "click here"

FAQ SECTION (required, append at the very end of the post):
- Add a ## Frequently Asked Questions section as the final section
- Include 3 to 5 questions that readers of this topic are likely to ask
- The questions MUST be different from any question already answered or addressed in the post body — look at the H2 headings and content and avoid duplicating those topics
- Format each as: ### Question text? followed by a concise 2-4 sentence answer
- Questions should be genuinely useful and reflect real search intent around the topic

OUTPUT FORMAT:
- Clean markdown only — start directly with the # title, no preamble or commentary
- Do not add any text before or after the post content

ORIGINAL TITLE: {title}

AUDIT FINDINGS:
{audit}

AVAILABLE SITE PAGES FOR INTERNAL LINKS (use these full URLs):
{site_pages}

ORIGINAL POST TO REWRITE:
{body_text}"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_site_pages(site_pages: list, domain: str = "") -> str:
    """
    Format site pages for prompt injection.
    Uses full URLs so Claude can place them directly into links.
    Caps at 100 pages to avoid token overflow.
    """
    if not site_pages:
        return "No sitemap pages available."

    # Normalise domain for building full URLs
    base = domain.rstrip("/") if domain else ""

    lines = []
    for page in site_pages[:100]:
        full_url = page.get("url", "")
        slug = page.get("slug", "")
        title = page.get("title", "")

        # Build full URL if we only have a slug
        if not full_url and slug and base:
            full_url = base + ("" if slug.startswith("/") else "/") + slug.lstrip("/")

        display_url = full_url or slug or "(no url)"
        lines.append(f"- {display_url} | {title}")

    return "\n".join(lines)


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences from a string."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
    return text.strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# claude-sonnet-4-6 pricing (USD per token)
_PRICE_IN  = 3.00  / 1_000_000   # $3.00 per million input tokens
_PRICE_OUT = 15.00 / 1_000_000   # $15.00 per million output tokens


def _usage_cost(usage) -> dict:
    """Convert an API usage object to a cost breakdown dict."""
    inp  = getattr(usage, "input_tokens",  0)
    out  = getattr(usage, "output_tokens", 0)
    return {
        "input_tokens":  inp,
        "output_tokens": out,
        "cost_usd":      inp * _PRICE_IN + out * _PRICE_OUT,
    }


def analyze_post(
    post: dict,
    site_pages: list,
    client: anthropic.Anthropic,
    domain: str = "",
) -> tuple[dict, dict]:
    """
    First pass: analyze the post and return (audit_dict, usage_dict).
    audit_dict falls back gracefully if JSON parsing fails.
    usage_dict contains input_tokens, output_tokens, cost_usd.
    """
    prompt = ANALYSIS_PROMPT.format(
        title=post.get("title", "Untitled"),
        word_count=post.get("word_count", 0),
        body_text=post.get("body_text", "")[:8000],
        site_pages=_format_site_pages(site_pages, domain),
    )

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    usage = _usage_cost(message.usage)
    raw   = _strip_code_fences(message.content[0].text)

    try:
        return json.loads(raw), usage
    except json.JSONDecodeError:
        audit = {
            "thin_sections": [],
            "outdated_claims": [],
            "missing_internal_links": [],
            "missing_external_links": [],
            "overall_word_count": post.get("word_count", 0),
            "verdict": "average",
            "_parse_error": raw[:500],
        }
        return audit, usage


def rewrite_post(
    post: dict,
    audit: dict,
    site_pages: list,
    client: anthropic.Anthropic,
    domain: str = "",
) -> tuple[str, dict]:
    """
    Second pass: produce a fully rewritten post as markdown.
    Returns (markdown_string, usage_dict).
    """
    prompt = REWRITE_PROMPT.format(
        title=post.get("title", "Untitled"),
        audit=json.dumps(audit, indent=2),
        site_pages=_format_site_pages(site_pages, domain),
        body_text=post.get("body_text", "")[:8000],
    )

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    return message.content[0].text.strip(), _usage_cost(message.usage)
