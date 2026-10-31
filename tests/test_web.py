import http.server
import threading

import pytest

import agent.web as web
from agent.web import Web, WebError, check_url, page_text, parse_results

DDG = '''<div class="result"><h2 class="result__title"><a rel="nofollow" class="result__a" href="https://docs.pytest.org/en/stable/fixtures.html">How to use <b>fixtures</b> &amp; more</a></h2>
<a class="result__snippet" href="https://docs.pytest.org/x">Fixtures can <b>request</b> other fixtures.</a></div>
<div class="result"><h2><a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Frealpython.com%2Fpytest%2F&amp;rut=abc">Real Python</a></h2></div>
<div class="result"><h2><a class="result__a" href="javascript:alert(1)">bad</a></h2><a class="result__snippet" href="x">no</a></div>
<div class="result"><h2><a class="result__a" href="https://example.org/third">Third</a></h2><a class="result__snippet" href="x">third snippet</a></div>'''


def test_search_results_are_parsed_unwrapped_and_cleaned():
    r = parse_results(DDG)
    assert r[0] == {"title": "How to use fixtures & more", "url": "https://docs.pytest.org/en/stable/fixtures.html", "snippet": "Fixtures can request other fixtures."}
    assert r[1]["url"] == "https://realpython.com/pytest/" and r[1]["snippet"] == ""
    assert [x["title"] for x in r] == ["How to use fixtures & more", "Real Python", "Third"]       # the javascript: link is dropped
    assert r[2]["snippet"] == "third snippet"                                                   # a snippet is matched to its own result only
    assert len(parse_results(DDG * 5, limit=2)) == 2 and parse_results("") == [] and parse_results("<html>garbage</a>") == []


def test_page_text_drops_scripts_styles_and_svg_titles():
    out = page_text("<html><head><title>My Page</title><style>p{}</style></head><body><svg><title>Menu</title></svg><script>evil()</script>"
                    "<h1>Hello</h1><p>one   two</p><p>three</p></body></html>")
    assert out == "My Page\nHello\none two\nthree" and "evil" not in out and "Menu" not in out
    assert page_text("") == "" and page_text("plain text") == "plain text" and "x" in page_text("<p>x<p>y</div></span>")


def test_web_search_formats_results_and_validates_the_query():
    w = Web(search=lambda q: [{"title": f"T {q}", "url": "https://a.example/1", "snippet": "s" * 500}], fetch_page=lambda u: "page")
    assert w.search("  pytest  ").startswith("1. T pytest - https://a.example/1\n   ssss") and len(w.search("x").splitlines()[1]) <= 203
    assert w.fetch("https://a.example/") == "page"
    for bad in (None, 5, "", "   ", "x" * 301, "a\0b", ["q"], b"q"):
        with pytest.raises(WebError):
            w.search(bad)


HOSTILE_URLS = ["http://127.0.0.1/", "http://localhost/", "http://[::1]/", "http://0.0.0.0/", "http://10.1.2.3/", "http://192.168.1.1/",
                "http://172.16.0.1/", "http://169.254.169.254/latest/meta-data/", "http://100.64.0.1/", "http://2130706433/", "http://0x7f.1/",
                "file:///etc/passwd", "ftp://example.com/", "gopher://example.com/", "javascript:alert(1)", "data:text/plain,hi", "//example.com/",
                "example.com", "", "http://", "http:///path", "http://exa mple.com/", "https://example.com/\nHost: evil", "http://a\0b.com/",
                None, 5, ["http://example.com"], "http://" + "a" * 2500 + ".com/"]


@pytest.mark.parametrize("url", HOSTILE_URLS, ids=[repr(u)[:30] for u in HOSTILE_URLS])
def test_hostile_urls_are_refused(url):
    with pytest.raises(WebError):
        check_url(url)
    with pytest.raises(WebError):
        web.fetch(url)


# ---- fetching from a local server (the safety check is relaxed for exactly that address) ----
class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        routes = {"/page": (200, "text/html; charset=utf-8", b"<html><title>Hi</title><body><p>hello world</p></body></html>"),
                  "/json": (200, "application/json", b'{"a": 1}'),
                  "/bin": (200, "application/octet-stream", b"\x00\x01"),
                  "/big": (200, "text/plain", b"x" * (web.MAX_BYTES + 50_000)),
                  "/latin": (200, "text/plain; charset=latin-1", "caf\xe9".encode("latin-1")),
                  "/liar": (200, "text/plain; charset=no-such-charset", b"abc"),
                  "/loop": (302, "text/plain", b""), "/private": (302, "text/plain", b""), "/missing": (404, "text/plain", b"nope")}
        status, kind, body = routes.get(self.path, (404, "text/plain", b""))
        self.send_response(status)
        if self.path == "/loop":
            self.send_header("Location", "/loop")
        if self.path == "/private":
            self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
        self.send_header("Content-Type", kind)
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def server(monkeypatch):
    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    real = web.check_url
    monkeypatch.setattr(web, "check_url", lambda url: url if url.startswith(base) else real(url))
    yield base
    srv.shutdown()


def test_fetch_returns_readable_text(server):
    assert web.fetch(server + "/page") == "Hi\nhello world"
    assert web.fetch(server + "/json") == '{"a": 1}'
    assert web.fetch(server + "/latin") == "café" and web.fetch(server + "/liar") == "abc"


@pytest.mark.parametrize("path", ["/bin", "/missing", "/loop", "/private"])
def test_fetch_refuses_non_text_errors_loops_and_redirects_to_private_addresses(server, path):
    with pytest.raises(WebError):
        web.fetch(server + path)


def test_fetch_never_reads_more_than_the_byte_limit(server):
    assert len(web.fetch(server + "/big")) == web.MAX_BYTES


# ---- live (pytest -m net) ----
@pytest.mark.net
def test_live_search_and_fetch():
    out = Web().search("python pytest fixtures")
    assert out.startswith("1. ") and "http" in out
    assert "fixture" in Web().fetch("https://docs.pytest.org/en/stable/how-to/fixtures.html").lower()
