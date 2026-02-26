import re
import requests
from bs4 import BeautifulSoup

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


def _is_blocked_page(html: str) -> bool:
    """Detect Cloudflare challenge/block pages that return 200."""
    lower = html[:5000].lower()
    signals = ["just a moment", "checking your browser", "cf-challenge", "challenge-platform"]
    return any(s in lower for s in signals)


def fetch_post(url: str) -> dict:
    """Fetch a blog post URL and return structured content."""
    response = None
    last_error = None

    for headers in _HEADER_SETS:
        try:
            resp = requests.get(url, headers=headers, timeout=20)
            resp.raise_for_status()
            if _is_blocked_page(resp.text):
                last_error = "Cloudflare blocked the request (challenge page returned)"
                continue
            response = resp
            break
        except Exception as e:
            last_error = str(e)
            continue

    if response is None:
        return {"error": last_error or "Failed to fetch the page", "url": url}

    soup = BeautifulSoup(response.text, "html.parser")

    # Extract title
    title = ""
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(strip=True)
    elif soup.title:
        title = soup.title.get_text(strip=True)

    # Find main content area - try selectors in order, skip tiny matches
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

    # Extract headings
    headings = []
    for tag in content_area.find_all(["h2", "h3"]):
        headings.append({"level": tag.name, "text": tag.get_text(strip=True)})

    # Extract links, split into internal and external
    domain = ""
    try:
        from urllib.parse import urlparse
        domain = urlparse(url).netloc
    except Exception:
        pass

    internal_links = []
    external_links = []
    for a in content_area.find_all("a", href=True):
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
    body_text = content_area.get_text(separator="\n", strip=True)
    body_text = re.sub(r"\n{3,}", "\n\n", body_text)

    word_count = len(body_text.split())

    # Extract slug from URL path
    from urllib.parse import urlparse
    path = urlparse(url).path.rstrip("/")
    slug = path.split("/")[-1] if "/" in path else path or "post"

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
