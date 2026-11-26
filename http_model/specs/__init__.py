# specs of programs over httplib2, run on the httplib2 model with run_spec(models, magics): a server answers each
# request by its path, so a spec's arguments, written into the path, choose the answer. Most specs state what the
# programs do with this server's answers, and a model checker, supplying answers of its own, finds counterexamples
# to them; head_has_no_body is stated of whatever a server answers

import errno
import gzip
import json
import zlib
import socket
import http.client
import urllib.parse

import httplib2

from PyMagic import always, need, spec
from http_model.magics import module_magics, reply
from stdlib_model.magics import stdlib_magics

REDIRECTS = (301, 302, 303, 307, 308)
FORTUNE = "You will write a spec today"


# ------------------------------------------------------------------ the server

def last(uri):
    # the last part of a uri's path, unquoted
    return urllib.parse.unquote(urllib.parse.urlsplit(uri).path.rsplit("/", 1)[1])


def server(uri):
    # the reply to a request, by its path: /status/404, /redirect/301, /gzip/<text>...
    path = urllib.parse.urlsplit(uri).path
    kind = path.split("/")[1]
    if kind == "status":
        return reply(json.dumps({"fortune": FORTUNE}).encode(), status=int(last(uri)))
    if kind == "redirect":
        return reply(status=int(last(uri)), headers={"location": "/final"})
    if kind == "nolocation":
        return reply(status=int(last(uri)))
    if kind == "final":
        return reply(b"final")
    if kind == "echo":
        return reply(json.dumps({"fortune": last(uri)}).encode())
    if kind == "gzip":
        return reply(gzip.compress(last(uri).encode()), headers={"content-encoding": "gzip"})
    if kind == "deflate":
        return reply(zlib.compress(last(uri).encode()), headers={"content-encoding": "deflate"})
    if kind == "header":
        _, _, name, value = path.split("/")
        return reply(headers={urllib.parse.unquote(name): urllib.parse.unquote(value)})
    if kind == "unreachable":
        return reply(b"made it", errors=[OSError(errno.ENETUNREACH, "Network is unreachable")] * int(last(uri)))
    if kind == "stale":
        return reply(b"made it", errors=[http.client.BadStatusLine("")] * int(last(uri)))
    if kind == "refused":
        return reply(errors=[ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused")])
    if kind == "lost":
        return reply(errors=[socket.gaierror(-2, "Name or service not known")])
    raise AssertionError(f"the server has no answer for {uri}")


def magics_with(request_magics):
    # an empty environment, a fixed clock, and request_magics for Http.request
    return {**module_magics({}), **stdlib_magics(now=1_700_000_000),
            httplib2.Http: {httplib2.Http.request: request_magics}}


magics = magics_with(lambda self, uri, *args, **kwargs: server(uri))


# ------------------------------------------------------------------ the programs

def get(path, method="GET"):
    return httplib2.Http().request("http://cookies.example" + path, method)


def fortune(path):
    # the fortune at path, or a consolation
    try:
        response, content = get(path)
    except httplib2.ServerNotFoundError:
        return "the cookie jar is lost"
    if response.status != 200:
        return f"no cookie today ({response.status} {response.reason})"
    return json.loads(content)["fortune"]


def raised(call):
    # the class of the exception call raises, or None
    try:
        call()
    except Exception as error:
        return type(error)
    return None


# ------------------------------------------------------------------ the specs

@spec
def a_cookie_or_a_consolation(status):
    # 200 gives the fortune, and any other status that isn't a redirect says why there's none
    need(isinstance(status, int) and 101 <= status <= 999 and status not in (*REDIRECTS, 300, 304))
    told = fortune(f"/status/{status}")
    always(told == FORTUNE if status == 200 else told.startswith(f"no cookie today ({status} "))


@spec
def a_redirect_is_followed(code):
    need(code in REDIRECTS)
    response, content = get(f"/redirect/{code}")
    always(response.status == 200 and response.previous.status == code and content == b"final")


@spec
def a_redirect_needs_its_location(code):
    # 300 goes without one: it's no redirect httplib2 follows by itself
    need(code in (300, *REDIRECTS))
    always(raised(lambda: get(f"/nolocation/{code}")) is (None if code == 300 else httplib2.RedirectMissingLocation))


@spec
def the_fortune_comes_back(text):
    need(isinstance(text, str))
    always(fortune("/echo/" + urllib.parse.quote(text, safe="")) == text)


@spec
def compressed_content_comes_back(encoding, text):
    need(encoding in ("gzip", "deflate") and isinstance(text, str))
    _, content = get(f"/{encoding}/" + urllib.parse.quote(text, safe=""))
    always(content == text.encode())


@spec
def headers_come_back_by_lowercase_name(name, value):
    need(isinstance(name, str) and isinstance(value, str))
    response, _ = get(f"/header/{urllib.parse.quote(name, safe='')}/{urllib.parse.quote(value, safe='')}")
    always(response[name.lower()] == value)


@spec
def head_has_no_body(status):
    # stated of whatever the server answers, but a redirect: a 302 or 303 is followed with a GET, which has a body
    need(isinstance(status, int) and 200 <= status <= 599 and status not in (*REDIRECTS, 300))
    response, content = get(f"/status/{status}", "HEAD")
    always(response.previous is not None or content == b"")


@spec
def an_unreachable_network_is_tried_twice(failures):
    need(isinstance(failures, int) and failures >= 0)
    always(raised(lambda: get(f"/unreachable/{failures}")) is (None if failures <= 1 else OSError))


@spec
def a_stale_connection_is_retried_once(failures):
    need(isinstance(failures, int) and failures >= 0)
    always(raised(lambda: get(f"/stale/{failures}")) is (None if failures <= 1 else http.client.BadStatusLine))


@spec
def network_errors_are_what_they_are():
    always(fortune("/lost/0") == "the cookie jar is lost")
    always(raised(lambda: get("/refused/0")) is ConnectionRefusedError)


specs = [
    a_cookie_or_a_consolation, a_redirect_is_followed, a_redirect_needs_its_location, the_fortune_comes_back,
    compressed_content_comes_back, headers_come_back_by_lowercase_name, head_has_no_body,
    an_unreachable_network_is_tried_twice, a_stale_connection_is_retried_once, network_errors_are_what_they_are,
]
