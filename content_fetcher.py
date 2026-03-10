import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse

# Try multiple header sets. Some Cloudflare configs block cloud IPs with
# bot-style UAs but allow browser-like UAs, and vice versa.
_HEADER_SETS = [
    {
        "User-Agent": "Mozilla/5.0 (compatible; BlogRevivalBot/1.0; +https://airanking.com)",
    },
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    },
]

CONTENT_SELECTORS = [
    ".post-content",
    ".entry-content",
    ".et_pb_post_content",
    ".et_pb_text_inner",
    ".post-body",
    ".article-body",
    ".blog-post",
    ".single-post",
    "#content",
    ".content",
    "article",
    "main",
]

# Minimum word count to accept a content area as the real post body.
# Prevents grabbing tiny related-post cards or sidebar widgets.
_MIN_CONTENT_WORDS = 100

# Below this threshold after extraction, the page is likely blocked or empty.
_MIN_USABLE_WORDS = 50

# Responses shorter than this (bytes) are likely soft-blocks (e.g. LiteSpeed
# returning 200 with an empty or near-empty body).
_SOFT_BLOCK_BYTES = 500


def _is_blocked_page(html: str) -> bool:
    """Detect Cloudflare challenge/block pages that return 200."""
    lower = html[:5000].lower()
    signals = [
        "just a moment", "checking your browser", "cf-challenge",
        "challenge-platform", "turnstile", "ray-id",
    ]
    return any(s in lower for s in signals)


# ---------------------------------------------------------------------------
# WordPress REST API fallback
# ---------------------------------------------------------------------------

def _try_wp_api(url: str) -> dict | None:
    """
    If the site is WordPress, fetch the post via the REST API.
    Returns a parsed result dict on success, None on failure.
    """
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path.rstrip("/")
    slug = path.split("/")[-1] if "/" in path else ""

    if not slug:
        return None

    api_url = f"{base}/wp-json/wp/v2/posts?slug={slug}&_fields=title,content,link"

    try:
        resp = requests.get(api_url, timeout=15, headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
        })
        resp.raise_for_status()
        posts = resp.json()
    except Exception:
        return None

    if not posts or not isinstance(posts, list):
        return None

    wp_post = posts[0]
    title = wp_post.get("title", {}).get("rendered", "")
    content_html = wp_post.get("content", {}).get("rendered", "")

    if not content_html:
        return None

    # Parse the rendered HTML content
    soup = BeautifulSoup(content_html, "html.parser")

    # Strip the title from HTML entities
    if title:
        title = BeautifulSoup(title, "html.parser").get_text(strip=True)

    return _parse_content(url, slug, title, soup, parsed.netloc)


# ---------------------------------------------------------------------------
# Shared content parser
# ---------------------------------------------------------------------------

def _parse_content(url: str, slug: str, title: str, content_soup, domain: str) -> dict:
    """Extract structured data from a BeautifulSoup content element."""

    # Extract headings
    headings = []
    for tag in content_soup.find_all(["h2", "h3"]):
        headings.append({"level": tag.name, "text": tag.get_text(strip=True)})

    # Extract links
    internal_links = []
    external_links = []
    for a in content_soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True)
        if not href or href.startswith("#") or href.startswith("mailto:"):
            continue
        if domain and domain in href:
            internal_links.append({"text": text, "href": href})
        elif href.startswith("/"):
            internal_links.append({"text": text, "href": href})
        elif href.startswith("http"):
            external_links.append({"text": text, "href": href})

    # Get clean body text
    body_text = content_soup.get_text(separator="\n", strip=True)
    body_text = re.sub(r"\n{3,}", "\n\n", body_text)
    word_count = len(body_text.split())

    return {
        "url": url,
        "slug": slug,
        "title": title,
        "headings": headings,
        "body_text": body_text,
        "internal_links": internal_links,
        "external_links": external_links,
        "word_count": word_count,
        "error": None,
    }


# ---------------------------------------------------------------------------
# Main fetch
# ---------------------------------------------------------------------------

def fetch_post(url: str) -> dict:
    """Fetch a blog post URL and return structured content."""
    parsed = urlparse(url)
    domain = parsed.netloc
    path = parsed.path.rstrip("/")
    slug = path.split("/")[-1] if "/" in path else path or "post"

    # -- Attempt 1: direct HTML fetch with header rotation --
    # Use a session so cookies (e.g. LiteSpeed LSCWP) persist across requests.
    response = None
    last_error = None
    homepage = f"{parsed.scheme}://{parsed.netloc}/"

    for headers in _HEADER_SETS:
        try:
            session = requests.Session()
            session.headers.update(headers)
            # Hit the homepage first to pick up anti-bot cookies
            try:
                session.get(homepage, timeout=10)
            except Exception:
                pass  # cookie priming is best-effort
            resp = session.get(url, timeout=20)
            resp.raise_for_status()
            if _is_blocked_page(resp.text):
                last_error = "Cloudflare blocked the request (challenge page returned)"
                continue
            # Detect soft-blocks: 200 but near-empty body
            if len(resp.content) < _SOFT_BLOCK_BYTES:
                last_error = "Server returned a near-empty response (likely soft-blocked)"
                continue
            response = resp
            break
        except Exception as e:
            last_error = str(e)
            continue

    # If we got a response, try to extract content
    if response is not None:
        soup = BeautifulSoup(response.text, "html.parser")

        title = ""
        h1 = soup.find("h1")
        if h1:
            title = h1.get_text(strip=True)
        elif soup.title:
            title = soup.title.get_text(strip=True)

        # Find main content area
        content_area = None
        for selector in CONTENT_SELECTORS:
            candidate = soup.select_one(selector)
            if candidate:
                words = len(candidate.get_text(strip=True).split())
                if words >= _MIN_CONTENT_WORDS:
                    content_area = candidate
                    break
        if not content_area:
            content_area = soup.body or soup

        body_text = content_area.get_text(separator="\n", strip=True)
        body_text = re.sub(r"\n{3,}", "\n\n", body_text)
        word_count = len(body_text.split())

        # If we got enough content, return it
        if word_count >= _MIN_USABLE_WORDS:
            return _parse_content(url, slug, title, content_area, domain)

        # Not enough content: likely a Cloudflare block or JS-rendered page
        last_error = (
            f"Page returned only {word_count} words "
            "(likely blocked by Cloudflare or requires JavaScript)"
        )

    # -- Attempt 2: WordPress REST API fallback --
    wp_result = _try_wp_api(url)
    if wp_result and wp_result.get("word_count", 0) >= _MIN_USABLE_WORDS:
        return wp_result

    # -- All attempts failed --
    return {
        "error": (
            f"{last_error or 'Failed to fetch the page'}. "
            "This site may be blocking cloud-hosted requests. "
            "Try pasting the post content manually."
        ),
        "url": url,
    }
