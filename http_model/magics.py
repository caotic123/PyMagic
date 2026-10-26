# magics that run the model on httplib2's own answers, so unlike the model they use httplib2: the pure
# functions draw httplib2's results, made into the model's values; the environment variables come from a
# mapping; and reply() is the server's side of a request

import sys
import itertools
import http.client
import urllib.parse
from types import FunctionType, SimpleNamespace

import httplib2

from stdlib_model.magics import stdlib_magics
from stdlib_model.zlib import Decompress

from . import AllHosts, ProxyInfo
from .decode import ZlibDecoder
from .iri2uri import IRI2URI


def unmodelled(function):
    # function as httplib2 defines it, calling httplib2's own functions even while the models stand in for them
    namespace = dict(vars(sys.modules[function.__module__]))
    return FunctionType(function.__code__, namespace, function.__name__, function.__defaults__, function.__closure__)

# httplib2's pure functions, as httplib2 defines them
real = SimpleNamespace(**{function.__name__: unmodelled(function) for function in (
    httplib2.parse_uri, httplib2.urlnorm, httplib2.iri2uri, httplib2.safename, httplib2.has_timeout,
    httplib2.proxy_info_from_url, httplib2.DeflateDecoder,
)})

def as_model_proxy(proxy):
    # httplib2's ProxyInfo as the model's
    copy = ProxyInfo(*proxy.astuple())
    copy.bypass_hosts = AllHosts if proxy.bypass_hosts is AllHosts.__model__ else proxy.bypass_hosts
    return copy

def as_model_decoder(decoder):
    # a fresh ZlibDecoder of httplib2's as the model's, with the model's zlib decompressor of the same kind
    copy = ZlibDecoder.__new__(ZlibDecoder)
    copy._decoder = Decompress(ZlibDecoder.WBITS_DEFLATE, b"")
    return copy

def results_of(original, unpack=False, converted=lambda result: result):
    # a magic generator giving the original's own result, computed once the model draws it: a spec run on these
    # checks httplib2 against the spec
    def generator(*args, **kwargs):
        result = converted(original(*args, **kwargs))
        yield from result if unpack else (result,)
    return generator

def module_magics(environ):
    # the magics of httplib2's functions: the pure ones draw httplib2's own results, and those reading
    # environment variables read them from environ (os.environ, or a dict that sets them)
    def try_value_or_env(to, value, env_key, default=None):
        return [environ.get(env_key), environ.get(env_key.upper())]

    def proxy_info_from_environment(method="http"):
        return [environ.get(f"{method}_proxy"), environ.get(f"{method}_proxy".upper())]

    def proxy_info_from_url(url, method="http", noproxy=None):
        if noproxy is None:
            yield environ.get("no_proxy")
            yield environ.get("NO_PROXY")
            noproxy = environ.get("no_proxy", environ.get("NO_PROXY", ""))
        yield as_model_proxy(real.proxy_info_from_url(url, method, noproxy))

    return {
        httplib2: {
            httplib2.parse_uri: results_of(real.parse_uri, unpack=True),
            httplib2.urlnorm: results_of(real.urlnorm, unpack=True),
            httplib2.safename: results_of(real.safename),
            httplib2.has_timeout: results_of(real.has_timeout),
            httplib2.try_value_or_env: try_value_or_env,
            httplib2.proxy_info_from_environment: proxy_info_from_environment,
            httplib2.proxy_info_from_url: proxy_info_from_url,
        },
        IRI2URI: {httplib2.iri2uri: results_of(real.iri2uri)},
        httplib2.decode: {httplib2.DeflateDecoder: results_of(real.DeflateDecoder, converted=as_model_decoder)},
    }

def reply(content=b"", status=200, reason=None, headers=None, version=11, errors=()):
    # the magics, by variable name, of a server that answers every round trip of a request the same way;
    # errors are the attempts that fail before one goes through
    return {
        "error": itertools.chain(errors, itertools.repeat(None)),
        "status": itertools.repeat(status),
        "reason": itertools.repeat(http.client.responses.get(status, "") if reason is None else reason),
        "version": itertools.repeat(version),
        "response_headers": itertools.repeat({} if headers is None else headers),
        "content": itertools.repeat(content),
    }

# an empty environment, a fixed clock, seeded randomness, and a server whose /user answers JSON and any other uri
# a 404: the magic generator of Http.request gets each request's arguments
magics = {
    **module_magics({}),
    **stdlib_magics(now=1_700_000_000),
    httplib2.Http: {
        httplib2.Http.request: lambda self, uri, *args, **kwargs: (
            reply(b'{"name": "lol"}', headers={"Content-Type": "application/json"})
            if urllib.parse.urlsplit(uri).path == "/user" else reply(b"no such page", status=404)
        ),
    },
}
