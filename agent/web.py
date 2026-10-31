"""Web search and page fetch for the research step. Standard library only; the search backend is swappable.
Fetching refuses non-http(s) URLs and any host that resolves to a private, loopback or link-local address, on every redirect hop."""
import html
import ipaddress
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

MAX_BYTES = 500_000
TIMEOUT = 10
MAX_REDIRECTS = 3
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15"


class WebError(Exception):
    pass


def check_url(url):
    """Return `url` if it is a public http(s) address, else raise WebError."""
    if not isinstance(url, str) or len(url) > 2000 or any(c in url for c in "\0\r\n"):
        raise WebError("url must be a single-line string")
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise WebError("only http and https URLs are allowed")
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP)}
    except (socket.gaierror, UnicodeError) as e:
        raise WebError(f"cannot resolve {parts.hostname}: {e}") from None
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%")[0])
        if not ip.is_global:
            raise WebError(f"{parts.hostname} resolves to a non-public address")
    return url


class _Redirects(urllib.request.HTTPRedirectHandler):
    max_redirections = MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip, self.title, self._in_title, self._title_done = [], 0, "", False, False

    def handle_starttag(self, tag, attrs):
        self.skip += tag in ("script", "style", "noscript", "svg")
        self._in_title = tag == "title" and not self._title_done   # only the page's own title, not <svg><title>
        if tag in ("p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4", "pre"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        self.skip -= tag in ("script", "style", "noscript", "svg") and self.skip > 0
        self._title_done = self._title_done or tag == "title"
        self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self.skip:
            self.parts.append(data)


def page_text(markup):
    p = _Text()
    p.feed(markup)
    body = re.sub(r"\n\s*\n+", "\n", re.sub(r"[ \t\r\f\v]+", " ", "".join(p.parts))).strip()
    title = " ".join(p.title.split())
    return f"{title}\n{body}" if title else body


def fetch(url):
    """Download one public text page and return its readable text (at most MAX_BYTES read)."""
    check_url(url)
    opener = urllib.request.build_opener(_Redirects)
    try:
        with opener.open(urllib.request.Request(url, headers={"User-Agent": _UA}), timeout=TIMEOUT) as r:
            kind = r.headers.get_content_type()
            if not (kind.startswith("text/") or kind in ("application/json", "application/xml", "application/xhtml+xml")):
                raise WebError(f"not a text page ({kind})")
            raw = r.read(MAX_BYTES)
            charset = r.headers.get_content_charset() or "utf-8"
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise WebError(f"fetch failed: {getattr(e, 'reason', e)}") from None
    try:
        text = raw.decode(charset, errors="replace")
    except LookupError:   # a made-up charset in the header
        text = raw.decode("utf-8", errors="replace")
    return page_text(text) if "html" in kind else text


_TITLE = re.compile(r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_SNIPPET = re.compile(r'class="result__snippet"[^>]*>(.*?)</a>', re.S)


def parse_results(markup, limit=5):
    out, hits = [], list(_TITLE.finditer(markup))
    for i, m in enumerate(hits):
        href, title = html.unescape(m.group(1)), m.group(2)
        found = _SNIPPET.search(markup, m.end(), hits[i + 1].start() if i + 1 < len(hits) else len(markup))
        snippet = found.group(1) if found else ""
        if "uddg=" in href:   # DuckDuckGo wraps links in a redirect
            href = urllib.parse.unquote(urllib.parse.parse_qs(urllib.parse.urlsplit(href).query).get("uddg", [href])[0])
        if href.startswith("//"):
            href = "https:" + href
        strip = lambda t: " ".join(html.unescape(re.sub(r"<[^>]+>", "", t or "")).split())
        if href.startswith("http"):
            out.append({"title": strip(title), "url": href, "snippet": strip(snippet)})
        if len(out) >= limit:
            break
    return out


def duckduckgo(query):
    """Keyless search through DuckDuckGo's HTML page. A scraper: it can break or be rate limited, so it is replaceable."""
    req = urllib.request.Request("https://html.duckduckgo.com/html/", data=urllib.parse.urlencode({"q": query}).encode(), headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            results = parse_results(r.read(MAX_BYTES).decode("utf-8", errors="replace"))
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise WebError(f"search failed: {getattr(e, 'reason', e)}") from None
    if not results:
        raise WebError("the search returned no results (blocked or rate limited?)")
    return results


class Web:
    """What the research tools use. Pass your own `search` and `fetch_page` to use another backend or to test offline."""

    def __init__(self, search=duckduckgo, fetch_page=fetch):
        self._search, self._fetch = search, fetch_page

    def search(self, query):
        if not isinstance(query, str) or not query.strip() or len(query) > 300 or "\0" in query:
            raise WebError("query must be a non-empty string of at most 300 characters")
        return "\n".join(f"{i}. {r['title']} - {r['url']}\n   {r['snippet'][:200]}" for i, r in enumerate(self._search(query.strip()), 1))

    def fetch(self, url):
        return self._fetch(url)
