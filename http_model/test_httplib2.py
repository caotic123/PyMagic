import ast
import sys
import gzip
import json
import random
import inspect
import pathlib
import errno
import socket
import itertools
import threading
import collections
import contextlib
import http.client
import urllib.parse
from types import NoneType
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httplib2
import pytest

from MagicF import BrokenVariant, Model, RunningUnstatedModel, get_models, run_model
from MagicF.model import model_members
import httplib2.auth
from http_model import _model_httplib2, auth, models
from http_model.decode import ZlibDecoder, raw_deflate
from http_model.magics import as_model_proxy, magics, module_magics, real, reply
from stdlib_model.magics import stdlib_magics
from stdlib_model.testing import improper_uses
from stdlib_model.zlib import Decompress


def all_magics(environ):
    # httplib2's functions with the environment environ, and the standard library's at a fixed time
    return {**module_magics(environ), **stdlib_magics(now=1_700_000_000)}

ORIGINAL_REQUEST = httplib2.Http.request


def answering(generator):
    # the magics with one generator for every request, in an empty environment
    return {**all_magics({}), httplib2.Http: {httplib2.Http.request: generator}}

def every_request(**answer):
    # the magics of a server that gives every request the same reply(**answer)
    return answering(lambda self, uri, *args, **kwargs: reply(**answer))

def modelled(magics, call, *args):
    return run_model(models, magics)(call, *args)

def client():
    return httplib2.Http(proxy_info=None, timeout=5)


# ------------------------------------------------------------------ the model and the real httplib2 side by side

def sent_headers(headers, body):
    # the headers the server sends: the exchange's own and the length of its body
    return {**headers, "Content-Length": str(len(body))}

@contextlib.contextmanager
def serving(routes):
    # a local server answering each path with its exchanges in turn, over and over:
    # {path: [(status, reason, headers, body), ...]}
    hits = collections.Counter()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def answer(self):
            path = urllib.parse.urlsplit(self.path).path
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            exchanges = routes[path]
            status, reason, headers, body = exchanges[hits[path] % len(exchanges)]
            hits[path] += 1
            self.send_response_only(status, reason)
            for name, value in sent_headers(headers, body).items():
                self.send_header(name, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        do_GET = do_HEAD = do_POST = do_PUT = do_DELETE = answer

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()

def draws(exchange, method):
    # the magic values the model draws for one exchange, by position: no error, then the response
    status, reason, headers, body = exchange
    yield from (None, status, reason, 11, sent_headers(headers, body))
    if method != "HEAD" and status not in (204, 304) and status >= 200:
        yield body

def served(routes):
    # the model's magics for the same routes: each path's exchanges go on where its last request left them
    answers = {path: itertools.cycle(exchanges) for path, exchanges in routes.items()}

    def generator(self, uri, method="GET", *args, **kwargs):
        exchanges = answers.get(urllib.parse.urlsplit(uri).path, ())
        return itertools.chain.from_iterable(draws(exchange, method) for exchange in exchanges)
    return answering(generator)

def described(response):
    if response is None:
        return None
    return (dict(response), response.status, response.reason, response.version, response.fromcache,
            described(response.previous))

def outcome(call):
    # what a request did, comparable between the real httplib2 and the model, where the standard library's C
    # functions are Python functions
    try:
        response, content = call()
    except Exception as error:
        message = str(error).replace("'function' object", "'builtin_function_or_method' object")
        return "raised", type(error), message, described(getattr(error, "response", None)), \
            getattr(error, "content", None)
    return "returned", described(response), content


JSON = (200, "OK", {"Content-Type": "application/json"}, b'{"name": "lol"}')
FOUND_USER = (302, "Found", {"Location": "/user"}, b"moved")

def get(path, **options):
    def request(http, base):
        for name, value in options.items():
            setattr(http, name, value)
        return http.request(base + path)
    return request

def post(path, **options):
    def request(http, base):
        for name, value in options.items():
            setattr(http, name, value)
        return http.request(base + path, "POST", body="a=1", headers={"Content-Type": "text/plain"})
    return request

def challenged(challenge):
    return 401, "Unauthorized", {"WWW-Authenticate": challenge}, b"who?"

def twice(request):
    def again(http, base):
        request(http, base)
        return request(http, base)
    return again

def with_credentials(http, base):
    http.add_credentials("joe", "secret")
    return http.request(base + "/secret")

# id: (routes, the request, the status it returns or the error it raises)
SCENARIOS = {
    "json": ({"/user": [JSON]}, get("/user"), 200),
    "not found": ({"/user": [(404, "Not Found", {}, b"no such user")]}, get("/user"), 404),
    "server error": ({"/user": [(500, "Internal Server Error", {"Retry-After": "5"}, b"oops")]}, get("/user"), 500),
    "head": ({"/user": [JSON]}, lambda http, base: http.request(base + "/user", "HEAD"), 200),
    "no content": ({"/user": [(204, "No Content", {}, b"")]}, lambda http, base: http.request(base + "/user", "DELETE"),
                   204),
    "query": ({"/search": [JSON]}, get("/search?q=lol#top"), 200),
    "gzip": ({"/z": [(200, "OK", {"Content-Encoding": "gzip"}, gzip.compress(b"squeezed"))]}, get("/z"), 200),
    "broken gzip": ({"/z": [(200, "OK", {"Content-Encoding": "gzip"}, b"not gzip")]}, get("/z"),
                    httplib2.FailedToDecompressContent),
    "empty gzip": ({"/z": [(200, "OK", {"Content-Encoding": "gzip"}, b"")]}, get("/z"), ValueError),
    "redirect": ({"/old": [FOUND_USER], "/user": [JSON]}, get("/old"), 200),
    "redirect chain": ({"/a": [(307, "Temporary Redirect", {"Location": "/b"}, b"")],
                        "/b": [(301, "Moved Permanently", {"Location": "/user"}, b"")], "/user": [JSON]}, get("/a"), 200),
    "permanent redirect": ({"/old": [(301, "Moved Permanently", {"Location": "/user"}, b"")], "/user": [JSON]},
                           get("/old"), 200),
    "redirect loop": ({"/loop": [(302, "Found", {"Location": "/loop"}, b"")]}, get("/loop"), httplib2.RedirectLimit),
    "no redirections left": ({"/old": [FOUND_USER], "/user": [JSON]},
                             lambda http, base: http.request(base + "/old", redirections=0), httplib2.RedirectLimit),
    "missing location": ({"/old": [(302, "Found", {}, b"")]}, get("/old"), httplib2.RedirectMissingLocation),
    "multiple choices": ({"/old": [(300, "Multiple Choices", {}, b"pick one")]}, get("/old"), 300),
    "redirects off": ({"/old": [FOUND_USER], "/user": [JSON]}, get("/old", follow_redirects=False), 302),
    "post found": ({"/form": [FOUND_USER], "/user": [JSON]}, post("/form"), 302),
    "post see other": ({"/form": [(303, "See Other", {"Location": "/user"}, b"")], "/user": [JSON]}, post("/form"), 200),
    "post permanent": ({"/form": [(308, "Permanent Redirect", {"Location": "/user"}, b"")], "/user": [JSON]},
                       post("/form"), 200),
    "post all redirects": ({"/form": [(307, "Temporary Redirect", {"Location": "/user"}, b"")], "/user": [JSON]},
                           post("/form", follow_all_redirects=True), 200),
    "basic auth": ({"/secret": [(401, "Unauthorized", {"WWW-Authenticate": 'Basic realm="lol"'}, b"who?"),
                                (200, "OK", {}, b"come in")]}, with_credentials, 200),
    "auth refused": ({"/secret": [(401, "Unauthorized", {"WWW-Authenticate": 'Basic realm="lol"'}, b"no")]},
                     with_credentials, 401),
    "no credentials": ({"/secret": [(401, "Unauthorized", {"WWW-Authenticate": 'Basic realm="lol"'}, b"no")]},
                       get("/secret"), 401),
    "digest auth": ({"/secret": [challenged('Digest realm="lol", nonce="abc", qop="auth", opaque="xyz"'),
                                 (200, "OK", {"Authentication-Info": 'nextnonce="def"'}, b"come in")]},
                    with_credentials, 200),
    "stale digest": ({"/secret": [challenged('Digest realm="lol", nonce="abc", qop="auth"'), (200, "OK", {}, b"in"),
                                  challenged('Digest realm="lol", nonce="new", qop="auth", stale=true'),
                                  (200, "OK", {}, b"in again")]}, twice(with_credentials), 200),
    # httplib2 raises its Unimplemented*OptionErrors with a message alone, so they fail with a TypeError
    "digest qop": ({"/secret": [challenged('Digest realm="lol", nonce="abc", qop="auth-int"')]}, with_credentials,
                   TypeError),
    "digest algorithm": ({"/secret": [challenged('Digest realm="lol", nonce="abc", algorithm=SHA-256')]},
                         with_credentials, TypeError),
    "wsse": ({"/secret": [challenged('WSSE realm="lol", profile="UsernameToken"'), (200, "OK", {}, b"in")]},
             with_credentials, 200),
    "hmac digest without snonce": ({"/secret": [challenged('HMACDigest realm="lol"')]}, with_credentials,
                                   TypeError),
    # httplib2 calls hashlib.sha1.new, which isn't there
    "hmac digest": ({"/secret": [challenged('HMACDigest realm="lol", snonce="abc"')]}, with_credentials,
                    AttributeError),
    # httplib2 reads the auth module through a local variable named auth, set later
    "google login": ({"/secret": [challenged('GoogleLogin realm="lol"')]}, with_credentials, UnboundLocalError),
    "malformed challenge": ({"/secret": [challenged("Negotiate")]}, with_credentials, httplib2.MalformedHeader),
    "errors as status": ({"/loop": [(302, "Found", {"Location": "/loop"}, b"")]},
                         get("/loop", force_exception_to_status_code=True), 500),
    "relative uri": ({}, lambda http, base: http.request("/user"), httplib2.RelativeURIError),
    "relative uri as status": ({}, lambda http, base: (setattr(http, "force_exception_to_status_code", True),
                                                       http.request("/user"))[1], 400),
    "unknown scheme": ({}, lambda http, base: http.request(base.replace("http", "ftp") + "/user"), KeyError),
    "bad port": ({}, lambda http, base: http.request("http://127.0.0.1:port/user"), http.client.InvalidURL),
    "only if cached": ({}, lambda http, base: http.request(base + "/user", headers={"Cache-Control": "only-if-cached"}),
                       504),
}

@pytest.mark.parametrize("routes, request_, expected", SCENARIOS.values(), ids=SCENARIOS.keys())
def test_model_answers_like_the_real_httplib2(routes, request_, expected):
    real_client = client()
    with serving(routes) as base:
        real = outcome(lambda: request_(real_client, base))
        real_client.close()
    model = outcome(lambda: modelled(served(routes), lambda: request_(client(), base)))
    assert model == real
    if isinstance(expected, int):
        assert real[0] == "returned" and real[1][1] == expected
    else:
        assert real[0] == "raised" and real[1] is expected

def test_timeout_is_the_real_one():
    with socket.create_server(("127.0.0.1", 0)) as silent:  # takes the connection, never answers
        uri = f"http://127.0.0.1:{silent.getsockname()[1]}/user"
        real = outcome(lambda: httplib2.Http(proxy_info=None, timeout=0.2).request(uri))
    timeout = every_request(errors=[socket.timeout("timed out")])
    model = outcome(lambda: modelled(timeout, lambda: httplib2.Http(proxy_info=None, timeout=0.2).request(uri)))
    assert model == real
    assert real[1] is TimeoutError

def test_refused_connection_is_the_real_one():
    with socket.create_server(("127.0.0.1", 0)) as closed:
        uri = f"http://127.0.0.1:{closed.getsockname()[1]}/user"
    real = outcome(lambda: client().request(uri))
    refused = every_request(errors=[ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused")])
    model = outcome(lambda: modelled(refused, lambda: client().request(uri)))
    assert model == real
    assert real[1] is ConnectionRefusedError


# ------------------------------------------------------------------ code using httplib2, run on the model

def fetch_json(uri):
    # the code under test: reads a JSON resource, None when there's none
    response, content = httplib2.Http(proxy_info=None).request(uri, headers={"Accept": "application/json"})
    if response.status == 404:
        return None
    if response.status != 200:
        raise LookupError(f"{uri} answered {response.status} {response.reason}")
    return json.loads(content)

def test_client_reads_the_json():
    assert modelled(magics, fetch_json, "http://api.test/user") == {"name": "lol"}

def test_client_reads_a_missing_page_as_none():
    assert modelled(magics, fetch_json, "http://api.test/nobody") is None

def test_client_raises_on_a_server_error():
    with pytest.raises(LookupError, match="http://api.test/user answered 503 Service Unavailable"):
        modelled(every_request(status=503), fetch_json, "http://api.test/user")

def test_client_fails_on_content_that_is_not_json():
    with pytest.raises(json.JSONDecodeError):
        modelled(every_request(content=b"<html>"), fetch_json, "http://api.test/user")

def test_client_follows_a_redirect_by_uri():
    def site(self, uri, *args, **kwargs):
        if uri == "http://api.test/me":
            return reply(status=302, headers={"Location": "/user"})
        return reply(b'{"name": "lol"}')
    assert modelled(answering(site), fetch_json, "http://api.test/me") == {"name": "lol"}

def test_client_gets_the_unknown_server():
    unknown = every_request(errors=[socket.gaierror(socket.EAI_NONAME, "Name or service not known")])
    with pytest.raises(httplib2.ServerNotFoundError, match="Unable to find the server at api.test"):
        modelled(unknown, fetch_json, "http://api.test:8080/user")

def test_magics_by_position():
    # the draws in order: the error of each attempt, then status, reason, version, headers and content
    by_position = answering(lambda self, uri, *args, **kwargs: [None, 200, "OK", 11, {}, b"[1, 2]"])
    assert modelled(by_position, fetch_json, "http://api.test/user") == [1, 2]


# ------------------------------------------------------------------ what only the model can do

def test_generator_gets_every_request_of_a_redirect():
    requests = []
    def site(self, uri, method="GET", *args, **kwargs):
        requests.append((uri, method))
        if uri.endswith("/form"):
            return reply(status=303, headers={"Location": "http://api.test/done"})
        return reply(b"done")
    response, content = modelled(answering(site), lambda: client().request("http://api.test/form", "POST", body="a"))
    assert content == b"done"
    assert requests == [("http://api.test/form", "POST"), ("http://api.test/done", "GET")]
    assert response.previous.status == 303
    assert response.previous["content-location"] == "http://api.test/form"

UNREACHABLE = OSError(errno.ENETUNREACH, "Network is unreachable")

def test_unreachable_network_is_tried_twice():
    once = every_request(content=b"made it", errors=[UNREACHABLE])
    assert modelled(once, lambda: client().request("http://api.test/"))[1] == b"made it"
    with pytest.raises(OSError, match="Network is unreachable"):
        modelled(every_request(errors=[UNREACHABLE, UNREACHABLE]), lambda: client().request("http://api.test/"))

def test_stale_connection_is_retried_once():
    hung_up = http.client.RemoteDisconnected("Remote end closed connection without response")
    once = every_request(content=b"made it", errors=[hung_up])
    assert modelled(once, lambda: client().request("http://api.test/"))[1] == b"made it"
    with pytest.raises(http.client.RemoteDisconnected):
        modelled(every_request(errors=[hung_up, hung_up]), lambda: client().request("http://api.test/"))

def test_timeout_as_status_code():
    def request():
        http = client()
        http.force_exception_to_status_code = True
        return http.request("http://api.test/")
    response, content = modelled(every_request(errors=[socket.timeout("timed out")]), request)
    assert (response.status, response.reason, content) == (408, "Request Timeout", b"Request Timeout")

def test_reply_reasons_default_to_the_status_phrase():
    response, _ = modelled(every_request(status=418), lambda: client().request("http://api.test/"))
    assert (response.status, response.reason, response["status"]) == (418, "I'm a Teapot", "418")

@pytest.mark.parametrize("answer, broken", [
    (dict(status=42), "101 <= status <= 999"),
    (dict(version=20), "version in"),
    (dict(headers={"Content-Length": 5}), "isinstance"),
    (dict(content="text"), "not bytes"),
    (dict(errors=[ValueError("not from the network")]), "not NoneType or OSError"),
], ids=["status", "version", "header value", "content", "error"])
def test_model_rejects_what_a_server_cannot_answer(answer, broken):
    with pytest.raises(BrokenVariant, match=broken):
        modelled(every_request(**answer), lambda: client().request("http://api.test/"))

def test_cache_is_not_modelled(tmp_path):
    with pytest.raises(RunningUnstatedModel, match="doesn't model the cache"):
        modelled(magics, lambda: httplib2.Http(cache=str(tmp_path)).request("http://api.test/user"))

def test_real_request_is_back_after_the_run():
    modelled(magics, fetch_json, "http://api.test/user")
    assert httplib2.Http.request is ORIGINAL_REQUEST


# ------------------------------------------------------------------ the module's functions, specs of httplib2's own

def comparable(value):
    # proxies by their fields and decoders by what they decode, httplib2's and the model's alike
    if hasattr(value, "astuple"):
        return "proxy", value.astuple(), getattr(value.bypass_hosts, "__name__", value.bypass_hosts)
    if hasattr(value, "_decoder"):
        return "decoder", value._decoder.copy().decompress(raw_deflate(b"sample"))
    return value

def result(call):
    try:
        return "returned", comparable(call())
    except Exception as error:
        return "raised", type(error), str(error)

def by_name(name):
    # the function as httplib2 has it when called, which is its model during a run
    return lambda *args: getattr(httplib2, name)(*args)

FUNCTION_CASES = [
    ("parse_uri", "http://example.com/a/b?q=1#top"), ("parse_uri", "mailto:joe@example.com"),
    ("parse_uri", "//example.com/path"), ("parse_uri", "/just/a/path?x"), ("parse_uri", ""),
    ("parse_uri", "a:b:c"), ("parse_uri", "http://example.com"), ("parse_uri", "file:///etc/hosts"),
    ("parse_uri", "?only=query"), ("parse_uri", "#only-fragment"), ("parse_uri", "http://[::1]:8080/x"),
    ("parse_uri", "http://a/p\nq?x\ny"), ("parse_uri", "http://a/#frag\nment"),
    ("urlnorm", "HTTP://Example.COM/A?b=C#d"), ("urlnorm", "http://example.com"), ("urlnorm", "http://example.com?"),
    ("urlnorm", "https://example.com?q"), ("urlnorm", "/relative"), ("urlnorm", "http:///no-authority"),
    ("urlnorm", "mailto:joe@example.com"),
    ("iri2uri", "http://\N{COMET}.com/\N{COMET}"), ("iri2uri", "http://bitworking.org/?fred=\N{COMET}#\N{COMET}"),
    ("iri2uri", "http://example.com/already%20escaped"), ("iri2uri", b"http://bytes.example/"),
    ("iri2uri", "http://[::1/broken"), ("iri2uri", "http://a..b/"), ("iri2uri", "http://example.com/\x85"),
    ("safename", "http://example.com/path?query=1"), ("safename", b"http://example.com/bytes"),
    ("safename", "http://" + "x" * 200), ("safename", "https://例え.jp/パス"),
    ("safename", "no scheme: here"), ("safename", b"\xff"),
    ("has_timeout", None), ("has_timeout", socket._GLOBAL_DEFAULT_TIMEOUT), ("has_timeout", 0), ("has_timeout", 2.5),
    ("proxy_info_from_url", "http://user:pass@proxy.example:3128", "http", ""),
    ("proxy_info_from_url", "socks5://proxy.example", "https", "*"),
    ("proxy_info_from_url", "socks4://proxy.example:1080", "http", "a.com,,.b.com"),
    ("proxy_info_from_url", "http://proxy.example:8080", "ftp", "  "),
    ("proxy_info_from_url", "http://proxy.example:port", "http", ""),
    ("proxy_info_from_url", "http://proxy.example", "ftp", ""),
    ("DeflateDecoder",),
]

@pytest.mark.parametrize("name, args", [(case[0], case[1:]) for case in FUNCTION_CASES],
                         ids=[f"{case[0]}{case[1:]!r}" for case in FUNCTION_CASES])
def test_function_model_gives_what_httplib2_gives(name, args):
    # the spec accepts httplib2's own result, and raises what httplib2 raises
    model_result = result(lambda: run_model(models, all_magics({}))(by_name(name), *args))
    assert model_result == result(lambda: getattr(httplib2, name)(*args))

ENVIRONMENT_NAMES = ["http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "no_proxy", "NO_PROXY",
                     "httplib2_test_limit", "HTTPLIB2_TEST_LIMIT",
                     *(f"httplib2_decode_limit_{name}" for name in ("hard", "safe", "ratio", "chunk")),
                     *(f"HTTPLIB2_DECODE_LIMIT_{name}" for name in ("HARD", "SAFE", "RATIO", "CHUNK"))]

ENVIRONMENT_CASES = [
    ({"httplib2_test_limit": "5"}, by_name("try_value_or_env"), (int, None, "httplib2_test_limit")),
    ({"HTTPLIB2_TEST_LIMIT": "7"}, by_name("try_value_or_env"), (int, None, "httplib2_test_limit")),
    ({"httplib2_test_limit": "x", "HTTPLIB2_TEST_LIMIT": "9"}, by_name("try_value_or_env"),
     (int, None, "httplib2_test_limit")),
    ({}, by_name("try_value_or_env"), (int, None, "httplib2_test_limit", 42)),
    ({"httplib2_test_limit": "4"}, by_name("try_value_or_env"), (float, "bad", "httplib2_test_limit")),
    ({"http_proxy": "http://proxy.example:3128"}, by_name("proxy_info_from_environment"), ()),
    ({"HTTP_PROXY": "http://proxy.example:3128", "no_proxy": "*"}, by_name("proxy_info_from_environment"), ()),
    ({"http_proxy": "", "HTTP_PROXY": "http://proxy.example"}, by_name("proxy_info_from_environment"), ()),
    ({}, by_name("proxy_info_from_environment"), ()),
    ({"http_proxy": "http://proxy.example"}, by_name("proxy_info_from_environment"), ("ftp",)),
    ({"https_proxy": "socks5://proxy.example:1080", "NO_PROXY": "a.com"}, by_name("proxy_info_from_environment"),
     ("https",)),
    ({"no_proxy": "", "NO_PROXY": "y.com"}, by_name("proxy_info_from_url"), ("http://proxy.example", "http")),
    ({"NO_PROXY": "x.com,.y.com"}, by_name("proxy_info_from_url"), ("http://proxy.example", "https")),
    ({"httplib2_decode_limit_hard": "100", "HTTPLIB2_DECODE_LIMIT_RATIO": "2.5"},
     lambda: httplib2.Http(proxy_info=None).limit_kwargs, ()),
]

@pytest.mark.parametrize("environ, call, args", ENVIRONMENT_CASES)
def test_environment_model_reads_what_httplib2_reads(monkeypatch, environ, call, args):
    for name in ENVIRONMENT_NAMES:
        monkeypatch.delenv(name, raising=False)
    for name, value in environ.items():
        monkeypatch.setenv(name, value)
    real_result = result(lambda: call(*args))
    for name in environ:
        monkeypatch.delenv(name)  # the model reads only its magics
    assert result(lambda: run_model(models, all_magics(environ))(call, *args)) == real_result

def one_function(name, generator):
    # the magics of one function, under the module of httplib2 that defines it
    function = getattr(httplib2, name)
    return {sys.modules[function.__module__]: {function: generator}}

def wrong_port():
    proxy = as_model_proxy(real.proxy_info_from_url("http://p:3128", "http", ""))
    proxy.proxy_port = 80
    return proxy

def model_decoder(wbits):
    # the model's ZlibDecoder with the model's zlib decompressor, made outside a run
    decoder = ZlibDecoder.__new__(ZlibDecoder)
    decoder._decoder = Decompress(wbits, b"")
    return decoder

def used_decoder():
    decoder = model_decoder(ZlibDecoder.WBITS_DEFLATE)
    decoder.flush()
    return decoder

SAFE_DIGEST = real.safename("http://a/b").split(",")[1]

@pytest.mark.parametrize("name, args, values", [
    pytest.param("parse_uri", ("a:b",), [None, None, "a:b", None, None], id="scheme left out"),
    pytest.param("parse_uri", ("a:b:c",), ["a:b", None, "c", None, None], id="colon in the scheme"),
    pytest.param("parse_uri", (":x",), ["", None, "x", None, None], id="empty scheme"),
    pytest.param("parse_uri", ("//host/path",), [None, None, "//host/path", None, None], id="authority left out"),
    pytest.param("parse_uri", ("//a/b",), [None, "a/b", "", None, None], id="path in the authority"),
    pytest.param("parse_uri", ("http://ab/c",), ["http", "a", "b/c", None, None], id="host cut short"),
    pytest.param("parse_uri", ("http://a/b",), ["http", "a", "/c", None, None], id="parts don't make the uri"),
    pytest.param("parse_uri", ("http://a/b?q",), ["http", "a", "/b?q", None, None], id="query in the path"),
    pytest.param("parse_uri", ("?a#b",), [None, None, "", "a#b", None], id="fragment in the query"),
    pytest.param("parse_uri", ("#a\nb",), [None, None, "", None, "a\nb"], id="newline in the fragment"),
    pytest.param("urlnorm", ("HTTP://a/b",), ["HTTP", "a", "/b", "HTTP://a/b"], id="uppercase scheme"),
    pytest.param("urlnorm", ("http://a",), ["http", "a", "", "http://a"], id="no request uri"),
    pytest.param("urlnorm", ("http://a/b#c",), ["http", "a", "/b", "http://a/b#c"], id="fragment kept"),
    pytest.param("iri2uri", ("http://a/\N{COMET}",), ["http://a/\N{COMET}"], id="IRI character left"),
    pytest.param("safename", ("http://a/b",), ["ab,0123"], id="wrong digest"),
    pytest.param("safename", ("http://a/b",), ["a/b," + SAFE_DIGEST], id="unsafe character"),
    pytest.param("safename", ("http://a/b",), ["other," + SAFE_DIGEST], id="another name"),
    pytest.param("has_timeout", (None,), [True], id="timeout"),
    pytest.param("proxy_info_from_url", ("http://p:3128", "http", ""), [wrong_port()], id="proxy port"),
    pytest.param("proxy_info_from_url", ("http://p:3128", "http", ""),
                 [as_model_proxy(real.proxy_info_from_url("http://p:3128", "http", "*"))], id="bypassed hosts"),
    pytest.param("DeflateDecoder", (), [model_decoder(ZlibDecoder.WBITS_AUTO_GZIP_ZLIB)], id="gzip decoder"),
    pytest.param("DeflateDecoder", (), [used_decoder()], id="used decoder"),
    pytest.param("try_value_or_env", (int, None, "LIMIT"), ["1", "2"], id="one variable, two values"),
])
def test_function_model_rejects_a_wrong_result(name, args, values):
    wrong = one_function(name, lambda *args, **kwargs: values)
    with pytest.raises(BrokenVariant):
        run_model(models, wrong)(by_name(name), *args)


# ------------------------------------------------------------------ the methods of Http

def state(http):
    return http.credentials.credentials, http.certificates.credentials, http.authorizations, http.connections

@pytest.mark.parametrize("calls", [
    lambda http: http.add_credentials("joe", "secret", "Example.COM"),
    lambda http: http.add_certificate("key.pem", "cert.pem", "Example.com", "pass"),
    lambda http: (http.add_credentials("joe", "secret"), http.clear_credentials()),
    lambda http: (http.add_credentials("joe", "secret"), http.add_certificate("k", "c", ""), http.close()),
], ids=["add credentials", "add certificate", "clear credentials", "close"])
def test_http_method_models_change_what_httplib2_changes(calls):
    def changed():
        http = httplib2.Http(proxy_info=None)
        calls(http)
        return state(http)
    assert run_model(models, all_magics({}))(changed) == changed()

def test_cleared_credentials_answer_no_challenge():
    def request(http, base):
        http.add_credentials("joe", "secret")
        http.clear_credentials()
        return http.request(base + "/secret")
    challenge = {"/secret": [(401, "Unauthorized", {"WWW-Authenticate": 'Basic realm="lol"'}, b"no")]}
    test_model_answers_like_the_real_httplib2(challenge, request, 401)


# ------------------------------------------------------------------ the hierarchy

def defined_classes(module):
    return {value for value in vars(module).values() if inspect.isclass(value) and value.__module__ == module.__name__}

HTTPLIB2_MODELS = [_model_httplib2]
COPIES = [entry for kind, entry in get_models(HTTPLIB2_MODELS) if kind is Model.COPY]

def test_every_public_function_method_and_class_is_modelled():
    public = {value for name, value in vars(httplib2).items()
              if inspect.isfunction(value) and not name.startswith("_") and value.__module__.startswith("httplib2")}
    methods = {value for name, value in vars(httplib2.Http).items()
               if inspect.isfunction(value) and not name.startswith("_")}
    by_kind = collections.defaultdict(set)
    for kind, entry in get_models(HTTPLIB2_MODELS):
        by_kind[kind].add(entry.__model__)
    assert by_kind.keys() == {Model.MODULE, Model.COPY, Model.CLASS}
    assert by_kind[Model.MODULE] == {httplib2, httplib2.error, sys.modules["httplib2.iri2uri"], httplib2.decode}
    assert by_kind[Model.CLASS] == {httplib2.Http}
    # every class but the cache, the connections and Http, which has its methods modelled instead
    classes = defined_classes(httplib2) | defined_classes(httplib2.error) | defined_classes(httplib2.decode)
    assert classes - by_kind[Model.COPY] == {httplib2.FileCache, httplib2.HTTPConnectionWithTimeout,
                                             httplib2.HTTPSConnectionWithTimeout, httplib2.Http}
    functions = {original for kind, entry in get_models(HTTPLIB2_MODELS) if kind is Model.MODULE
                 for _, _, original in model_members(entry)}
    assert functions == public
    assert {original for _, _, original in model_members(_model_httplib2._model_Http)} == \
        methods | {httplib2.Http.__init__}

def test_httplib2s_classes_are_the_copies_in_a_run():
    def resolved():
        return [getattr(sys.modules[copy.__model__.__module__], copy.__model__.__name__) for copy in COPIES]
    assert run_model(models, magics)(resolved) == COPIES
    assert resolved() == [copy.__model__ for copy in COPIES]
    response_is_one = lambda: isinstance(client().request("http://api.test/user")[0], httplib2.Response)
    assert run_model(models, magics)(response_is_one)

PLAIN = (int, float, str, bytes, bool, NoneType, tuple, frozenset)
NOT_COPIED = {"__module__", "__dict__", "__weakref__", "__model__", "__model_kind__"}

def properties(cls):
    return {name: value for name, value in vars(cls).items() if name not in NOT_COPIED}

def parameters(member):
    # the names, kinds and defaults of a method's parameters, leaving out annotations, which name the copies
    function = getattr(member, "__func__", member)
    return [(p.name, p.kind, p.default) for p in inspect.signature(function).parameters.values()]

@pytest.mark.parametrize("copy", COPIES, ids=[copy.__name__ for copy in COPIES])
def test_copy_has_the_properties_of_its_original(copy):
    original = copy.__model__
    copied, own = properties(copy), properties(original)
    assert copied.keys() == own.keys()
    assert {name: value for name, value in copied.items() if isinstance(value, PLAIN)} == \
        {name: value for name, value in own.items() if isinstance(value, PLAIN)}
    for name, value in own.items():
        if inspect.isfunction(getattr(value, "__func__", value)):
            assert parameters(copied[name]) == parameters(value), name
        assert type(copied[name]) is type(value), name
    assert copy.__doc__ == original.__doc__
    assert [getattr(base, "__model__", base) for base in copy.__bases__] == list(original.__bases__)

def test_making_an_http_reads_the_environment():
    server_only = {httplib2.Http: {httplib2.Http.request: lambda self, uri, *args, **kwargs: reply(b"{}")}}
    with pytest.raises(RunningUnstatedModel, match="try_value_or_env.*no magics were given"):
        run_model(models, server_only)(fetch_json, "http://api.test/user")
    # the model of Http alone models only the network
    assert run_model([_model_httplib2._model_Http], server_only)(fetch_json, "http://api.test/user") == {}


# ------------------------------------------------------------------ the model needs no httplib2

AUTH_CASES = ['Basic realm="lol"', 'Basic dXNlcjpwYXNz==', 'Digest realm="a", nonce="b\\"c", qop="auth,auth-int"',
              'Basic realm="x", Digest realm=y, nonce=z', 'Negotiate', 'Basic realm="a""b"', 'Basic realm="x" junk',
              'Basic realm = "x" ,', 'Basic tok ==', 'basic r=1, BASIC r=2', 'Basic realm="\\x"', '\tBasic\ta="\tb"',
              ', Basic realm=x', '']
PIECES = ["Basic", "Digest", "realm", "=", "==", ",", " ", "\t", '"', "\\", "x41", '""', "a", "Z9", "-._~+/", "!#$",
          ";", "\n", "\r", "\u00e9", "qop", "auth,auth-int"]

def parsed(parse, header, name):
    try:
        return "returned", parse({name: header}, name)
    except Exception as error:
        return "raised", type(error).__name__, str(error)

def random_headers(count, seed=7):
    pick = random.Random(seed)
    return ["".join(pick.choice(PIECES) for _ in range(pick.randint(1, 12))) for _ in range(count)]

@pytest.mark.parametrize("parse", ["_parse_www_authenticate", "_parse_authentication_info"])
def test_challenge_parser_parses_as_pyparsing_does(parse):
    name = "www-authenticate" if parse == "_parse_www_authenticate" else "authentication-info"
    for header in AUTH_CASES + random_headers(3000):
        assert parsed(getattr(auth, parse), header, name) == parsed(getattr(httplib2.auth, parse), header, name), header

MODEL_FILES = ["__init__.py", "error.py", "iri2uri.py", "decode.py", "auth.py"]

@pytest.mark.parametrize("file", MODEL_FILES)
def test_model_names_httplib2_only_for_what_it_models(file):
    # httplib2 is named in the @model() decorators and nowhere else in the code of the model
    tree = ast.parse((pathlib.Path(__file__).parent / file).read_text())
    in_decorators = {id(node) for definition in ast.walk(tree) if isinstance(definition, (ast.FunctionDef, ast.ClassDef))
                     for decorator in definition.decorator_list for node in ast.walk(decorator)}
    uses = [node.lineno for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id in ("httplib2", "IRI2URI")
            and isinstance(node.ctx, ast.Load) and id(node) not in in_decorators]
    assert uses == []

@pytest.mark.parametrize("file", MODEL_FILES)
def test_model_reaches_the_standard_library_only_through_its_models(file):
    assert improper_uses(pathlib.Path(__file__).parent / file, models) == []

PROJECT = pathlib.Path(__file__).parents[1]
MODEL_FOLDERS = [PROJECT / "http_model", PROJECT / "stdlib_model"]

# what a model may not run: the libraries modelled, which run in C or reach the outside world
NOT_RUN = ("httplib2", "pyparsing", "hashlib", "_hashlib", "hmac", "zlib", "binascii", "re", "sre_compile",
           "sre_parse", "_sre", "unicodedata", "stringprep", "encodings.idna", "time", "random", "_random", "socket",
           "_socket", "ssl", "_ssl", "http.client", "email", "errno")

def from_a_model(frame):
    # whether the nearest frame of this project's code is a model's, rather than MagicF's, a test's or the magics'
    while frame is not None:
        path = pathlib.Path(frame.f_code.co_filename)
        if PROJECT in path.parents:
            return any(folder in path.parents for folder in MODEL_FOLDERS) and \
                path.name not in ("magics.py", "testing.py") and not path.name.startswith("test_")
        frame = frame.f_back
    return False

def module_called(frame, event, arg):
    if event == "call":
        return frame.f_globals.get("__name__", "")
    if event == "c_call":
        owner = getattr(arg, "__self__", None)
        return getattr(arg, "__module__", None) or (owner.__name__ if inspect.ismodule(owner) else
                                                    type(owner).__module__)
    return None

def modules_run(magics, call):
    # the modules of the functions the models call, themselves or through what they call, while call runs on the
    # models, once they are in
    run = set()

    def profile(frame, event, arg):
        module = module_called(frame, event, arg)
        if module and from_a_model(frame.f_back if event == "call" else frame):
            run.add(module)

    def profiled():
        sys.setprofile(profile)
        try:
            return call()
        finally:
            sys.setprofile(None)
    outcome(lambda: modelled(magics, profiled))
    return run

REQUESTS = {
    **{name: SCENARIOS[name][:2] for name in ["json", "gzip", "redirect chain", "basic auth", "stale digest", "wsse",
                                              "errors as status", "relative uri as status"]},
    "unicode host": ({"/user": [JSON]}, lambda http, base: http.request("http://b\u00fccher.example/user")),
    "header template": ({"/user": [JSON]}, lambda http, base: http.request(base + "/user", headers={"x": "a\\nb"})),
}

@pytest.mark.parametrize("routes, request_", REQUESTS.values(), ids=REQUESTS.keys())
def test_requests_run_none_of_the_libraries_modelled(routes, request_):
    run = modules_run(served(routes), lambda: request_(client(), "http://api.test"))
    assert {module for module in run if module.startswith(NOT_RUN) and
            any(module == name or module.startswith(name + ".") for name in NOT_RUN)} == set()
    assert "http_model" in run or "builtins" in run
