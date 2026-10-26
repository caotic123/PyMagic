# a model of httplib2 (0.32), laid out like httplib2: this file models httplib2/__init__.py, its functions and
# its Http class, and its model holds the models of the submodules error.py, iri2uri.py and decode.py, as
# httplib2 holds them. The model runs without httplib2's code: httplib2 is named only in the @model() decorators,
# for what each model stands in for, and for the modelled functions that models call, which are models in a run.
# What the models build on is copied from httplib2. Effects draw magic(): the server's side of each request and
# the environment variables read. The pure functions are specs: their results are magic, and constraints state
# what makes a result right
# (the copies are from httplib2 0.32, MIT license, Copyright 2006 Joe Gregorio)

import copy
import hmac
import time
import base64
import random
import string
import zlib
import hashlib
import functools
import urllib.parse
from types import NoneType

import httplib2
from httplib2 import proxy_info_from_environment, proxy_info_from_url, try_value_or_env  # models, in a run

from PyMagic import RunningUnstatedModel, constraint, magic, model
from stdlib_model import models as stdlib_models
from stdlib_model import re
from stdlib_model.email.message import Message
from stdlib_model.errno import EADDRNOTAVAIL, ENETUNREACH
from stdlib_model.http.client import (
    BadStatusLine, HTTPException, HTTPResponse, ResponseNotReady, _get_hostport, _validate_host,
)
from stdlib_model.socket import _GLOBAL_DEFAULT_TIMEOUT, gaierror, timeout

from . import auth
from .decode import DeflateDecoder, LimitDecoder, ZlibDecoder, _model_decode
from .error import (
    FailedToDecompressContent, HttpLib2ErrorWithResponse, ProxiesUnavailableError, RedirectLimit,
    RedirectMissingLocation, RelativeURIError, ServerNotFoundError, UnimplementedDigestAuthOptionError,
    UnimplementedHmacDigestAuthOptionError, _model_error,
)
from .iri2uri import _model_iri2uri, iri2uri


# ------------------------------------------------------------------ copied from httplib2

HTTPLIB2_VERSION = "0.32.0"  # the version modelled, which the user-agent names

# A request will be tried 'RETRIES' times if it fails at the socket/connection level.
RETRIES = 2

# The number of redirections to follow before giving up.
DEFAULT_MAX_REDIRECTS = 5

# Which headers are hop-by-hop headers by default
HOP_BY_HOP = [
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
]

# https://tools.ietf.org/html/rfc7231#section-8.1.3
SAFE_METHODS = ("GET", "HEAD", "OPTIONS", "TRACE")

# To change, assign to `Http().redirect_codes`
REDIRECT_CODES = frozenset((300, 301, 302, 303, 307, 308))

# the schemes httplib2 has a connection class for, the keys of its SCHEME_TO_CONNECTION
CONNECTION_SCHEMES = ("http", "https")

socks = None  # PySocks isn't part of the model

_md5 = hashlib.md5
_sha = hashlib.sha1


def _get_end2end_headers(response):
    hopbyhop = list(HOP_BY_HOP)
    hopbyhop.extend([x.strip() for x in response.get("connection", "").split(",")])
    return [header for header in list(response.keys()) if header not in hopbyhop]


_missing = object()


def _errno_from_exception(e):
    errno = getattr(e, "errno", _missing)
    if errno is not _missing:
        return errno

    # socket.error and common wrap in .args
    args = getattr(e, "args", None)
    if args:
        return _errno_from_exception(args[0])

    # pysocks.ProxyError wraps in .socket_err
    socket_err = getattr(e, "socket_err", None)
    if socket_err:
        return _errno_from_exception(socket_err)

    return None


def until(text, stops):
    # text up to its first character in stops
    for index, character in enumerate(text):
        if character in stops:
            return text[:index]
    return text


def parse_uri(uri):
    """Parses a URI using the regex given in Appendix B of RFC 3986.

        (scheme, authority, path, query, fragment) = parse_uri(uri)
    """
    # ^(([^:/?#]+):)?(//([^/?#]*))?([^?#]*)(\?([^#]*))?(#(.*))? matched by hand: each part takes all it can, and
    # the fragment, as ".*", stops at a line break
    scheme = until(uri, ":/?#")
    if scheme and uri[len(scheme):len(scheme) + 1] == ":":
        rest = uri[len(scheme) + 1:]
    else:
        scheme, rest = None, uri
    authority = None
    if rest.startswith("//"):
        authority = until(rest[2:], "/?#")
        rest = rest[2 + len(authority):]
    path = until(rest, "?#")
    rest = rest[len(path):]
    query = None
    if rest.startswith("?"):
        query = until(rest[1:], "#")
        rest = rest[1 + len(query):]
    fragment = until(rest[1:], "\n") if rest.startswith("#") else None
    return (scheme, authority, path, query, fragment)


def urlnorm(uri):
    (scheme, authority, path, query, fragment) = parse_uri(uri)
    if not scheme or not authority:
        raise RelativeURIError("Only absolute URIs are allowed. uri = %s" % uri)
    authority = authority.lower()
    scheme = scheme.lower()
    if not path:
        path = "/"
    # Could do syntax based normalization of the URI before
    # computing the digest. See Section 6.2.2 of Std 66.
    request_uri = query and "?".join([path, query]) or path
    scheme = scheme.lower()
    defrag_uri = scheme + "://" + authority + request_uri
    return scheme, authority, request_uri, defrag_uri


def normalize_space_sub(repl, string):
    # NORMALIZE_SPACE.sub(repl, string) of the regular expression (?:\r\n)?[ \t]+, matched by hand: each run of
    # spaces and tabs, with a line break just before it, becomes repl, a template of re's
    template = re.parse_template(repl, 0, {})
    text, at = [], 0
    while at < len(string):
        start = at + 2 if string.startswith("\r\n", at) and string[at + 2:at + 3] in (" ", "\t") else at
        end = start
        while end < len(string) and string[end] in " \t":
            end += 1
        if end > start:
            text.append(re.expand_template(template, [string[at:end]]))
            at = end
        else:
            text.append(string[at])
            at += 1
    return "".join(text)


def _normalize_headers(headers):
    # as httplib2 has it, the header's value is the template and the space the text, so a value with a backslash
    # is expanded as a template, and spaces in it stay
    return dict(
        [
            (_convert_byte_str(key).lower(), normalize_space_sub(_convert_byte_str(value), " ").strip(),)
            for (key, value) in headers.items()
        ]
    )


def _convert_byte_str(s):
    if not isinstance(s, str):
        return str(s, "utf-8")
    return s


def _parse_cache_control(headers):
    retval = {}
    if "cache-control" in headers:
        parts = headers["cache-control"].split(",")
        parts_with_args = [
            tuple([x.strip().lower() for x in part.split("=", 1)]) for part in parts if -1 != part.find("=")
        ]
        parts_wo_args = [(name.strip().lower(), 1) for name in parts if -1 == name.find("=")]
        retval = dict(parts_with_args + parts_wo_args)
    return retval


def _decompressContent(response, new_content, limit_kwargs):
    content = new_content
    encoding_header = "content-encoding"
    encoding = response.get(encoding_header, None)
    limit_wrap = functools.partial(LimitDecoder, **limit_kwargs)
    try:
        if encoding in ["gzip", "deflate", "zlib"]:
            try:
                content = limit_wrap(ZlibDecoder()).consume_bytes(new_content, 0)
            except (IOError, zlib.error):
                content = limit_wrap(DeflateDecoder()).consume_bytes(new_content, 0)
            response["content-length"] = str(len(content))
            # Record the historical presence of the encoding in a way the won't interfere.
            response["-content-encoding"] = response.pop(encoding_header)
    except (IOError, zlib.error):
        content = ""
        raise FailedToDecompressContent(
            "Content purported to be compressed with %s but failed to decompress." % encoding, response, content,
        )
    return content


def _cnonce():
    dig = _md5(
        ("%s:%s" % (time.ctime(), ["0123456789"[random.randrange(0, 9)] for i in range(20)])).encode("utf-8")
    ).hexdigest()
    return dig[:16]


def _wsse_username_token(cnonce, iso_now, password):
    return (
        base64.b64encode(_sha(("%s%s%s" % (cnonce, iso_now, password)).encode("utf-8")).digest()).strip().decode("utf-8")
    )


@model(httplib2.Authentication)
class Authentication(object):
    def __init__(self, credentials, host, request_uri, headers, response, content, http):
        (scheme, authority, path, query, fragment) = parse_uri(request_uri)
        self.path = path
        self.host = host
        self.credentials = credentials
        self.http = http

    def depth(self, request_uri):
        (scheme, authority, path, query, fragment) = parse_uri(request_uri)
        return request_uri[len(self.path) :].count("/")

    def inscope(self, host, request_uri):
        # XXX Should we normalize the request_uri?
        (scheme, authority, path, query, fragment) = parse_uri(request_uri)
        return (host == self.host) and path.startswith(self.path)

    def request(self, method, request_uri, headers, content):
        """Modify the request headers to add the appropriate
        Authorization header. Over-rise this in sub-classes."""
        pass

    def response(self, response, content):
        """Gives us a chance to update with new nonces
        or such returned from the last authorized response.
        Over-rise this in sub-classes if necessary.

        Return TRUE is the request is to be retried, for
        example Digest may return stale=true.
        """
        return False

    def __eq__(self, auth):
        return False

    def __ne__(self, auth):
        return True

    def __lt__(self, auth):
        return True

    def __gt__(self, auth):
        return False

    def __le__(self, auth):
        return True

    def __ge__(self, auth):
        return False

    def __bool__(self):
        return True


@model(httplib2.BasicAuthentication)
class BasicAuthentication(Authentication):
    def __init__(self, credentials, host, request_uri, headers, response, content, http):
        Authentication.__init__(self, credentials, host, request_uri, headers, response, content, http)

    def request(self, method, request_uri, headers, content):
        """Modify the request headers to add the appropriate
        Authorization header."""
        headers["authorization"] = "Basic " + base64.b64encode(
            ("%s:%s" % self.credentials).encode("utf-8")
        ).strip().decode("utf-8")


@model(httplib2.DigestAuthentication)
class DigestAuthentication(Authentication):
    """Only do qop='auth' and MD5, since that
    is all Apache currently implements"""

    def __init__(self, credentials, host, request_uri, headers, response, content, http):
        Authentication.__init__(self, credentials, host, request_uri, headers, response, content, http)
        self.challenge = auth._parse_www_authenticate(response, "www-authenticate")["digest"]
        qop = self.challenge.get("qop", "auth")
        self.challenge["qop"] = ("auth" in [x.strip() for x in qop.split()]) and "auth" or None
        if self.challenge["qop"] is None:
            raise UnimplementedDigestAuthOptionError("Unsupported value for qop: %s." % qop)
        self.challenge["algorithm"] = self.challenge.get("algorithm", "MD5").upper()
        if self.challenge["algorithm"] != "MD5":
            raise UnimplementedDigestAuthOptionError(
                "Unsupported value for algorithm: %s." % self.challenge["algorithm"]
            )
        self.A1 = "".join([self.credentials[0], ":", self.challenge["realm"], ":", self.credentials[1],])
        self.challenge["nc"] = 1

    def request(self, method, request_uri, headers, content, cnonce=None):
        """Modify the request headers"""
        H = lambda x: _md5(x.encode("utf-8")).hexdigest()
        KD = lambda s, d: H("%s:%s" % (s, d))
        A2 = "".join([method, ":", request_uri])
        self.challenge["cnonce"] = cnonce or _cnonce()
        request_digest = '"%s"' % KD(
            H(self.A1),
            "%s:%s:%s:%s:%s"
            % (
                self.challenge["nonce"],
                "%08x" % self.challenge["nc"],
                self.challenge["cnonce"],
                self.challenge["qop"],
                H(A2),
            ),
        )
        headers["authorization"] = (
            'Digest username="%s", realm="%s", nonce="%s", '
            'uri="%s", algorithm=%s, response=%s, qop=%s, '
            'nc=%08x, cnonce="%s"'
        ) % (
            self.credentials[0],
            self.challenge["realm"],
            self.challenge["nonce"],
            request_uri,
            self.challenge["algorithm"],
            request_digest,
            self.challenge["qop"],
            self.challenge["nc"],
            self.challenge["cnonce"],
        )
        if self.challenge.get("opaque"):
            headers["authorization"] += ', opaque="%s"' % self.challenge["opaque"]
        self.challenge["nc"] += 1

    def response(self, response, content):
        if "authentication-info" not in response:
            challenge = auth._parse_www_authenticate(response, "www-authenticate").get("digest", {})
            if "true" == challenge.get("stale"):
                self.challenge["nonce"] = challenge["nonce"]
                self.challenge["nc"] = 1
                return True
        else:
            updated_challenge = auth._parse_authentication_info(response, "authentication-info")

            if "nextnonce" in updated_challenge:
                self.challenge["nonce"] = updated_challenge["nextnonce"]
                self.challenge["nc"] = 1
        return False


@model(httplib2.HmacDigestAuthentication)
class HmacDigestAuthentication(Authentication):
    """Adapted from Robert Sayre's code and DigestAuthentication above."""

    __author__ = "Thomas Broyer (t.broyer@ltgt.net)"

    def __init__(self, credentials, host, request_uri, headers, response, content, http):
        Authentication.__init__(self, credentials, host, request_uri, headers, response, content, http)
        challenge = auth._parse_www_authenticate(response, "www-authenticate")
        self.challenge = challenge["hmacdigest"]
        # TODO: self.challenge['domain']
        self.challenge["reason"] = self.challenge.get("reason", "unauthorized")
        if self.challenge["reason"] not in ["unauthorized", "integrity"]:
            self.challenge["reason"] = "unauthorized"
        self.challenge["salt"] = self.challenge.get("salt", "")
        if not self.challenge.get("snonce"):
            raise UnimplementedHmacDigestAuthOptionError(
                "The challenge doesn't contain a server nonce, or this one is empty."
            )
        self.challenge["algorithm"] = self.challenge.get("algorithm", "HMAC-SHA-1")
        if self.challenge["algorithm"] not in ["HMAC-SHA-1", "HMAC-MD5"]:
            raise UnimplementedHmacDigestAuthOptionError(
                "Unsupported value for algorithm: %s." % self.challenge["algorithm"]
            )
        self.challenge["pw-algorithm"] = self.challenge.get("pw-algorithm", "SHA-1")
        if self.challenge["pw-algorithm"] not in ["SHA-1", "MD5"]:
            raise UnimplementedHmacDigestAuthOptionError(
                "Unsupported value for pw-algorithm: %s." % self.challenge["pw-algorithm"]
            )
        if self.challenge["algorithm"] == "HMAC-MD5":
            self.hashmod = _md5
        else:
            self.hashmod = _sha
        if self.challenge["pw-algorithm"] == "MD5":
            self.pwhashmod = _md5
        else:
            self.pwhashmod = _sha
        self.key = "".join(
            [
                self.credentials[0],
                ":",
                self.pwhashmod.new("".join([self.credentials[1], self.challenge["salt"]])).hexdigest().lower(),
                ":",
                self.challenge["realm"],
            ]
        )
        self.key = self.pwhashmod.new(self.key).hexdigest().lower()

    def request(self, method, request_uri, headers, content):
        """Modify the request headers"""
        keys = _get_end2end_headers(headers)
        keylist = "".join(["%s " % k for k in keys])
        headers_val = "".join([headers[k] for k in keys])
        created = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        cnonce = _cnonce()
        request_digest = "%s:%s:%s:%s:%s" % (method, request_uri, cnonce, self.challenge["snonce"], headers_val,)
        request_digest = hmac.new(self.key, request_digest, self.hashmod).hexdigest().lower()
        headers["authorization"] = (
            'HMACDigest username="%s", realm="%s", snonce="%s",'
            ' cnonce="%s", uri="%s", created="%s", '
            'response="%s", headers="%s"'
        ) % (
            self.credentials[0],
            self.challenge["realm"],
            self.challenge["snonce"],
            cnonce,
            request_uri,
            created,
            request_digest,
            keylist,
        )

    def response(self, response, content):
        challenge = auth._parse_www_authenticate(response, "www-authenticate").get("hmacdigest", {})
        if challenge.get("reason") in ["integrity", "stale"]:
            return True
        return False


@model(httplib2.WsseAuthentication)
class WsseAuthentication(Authentication):
    """This is thinly tested and should not be relied upon.
    At this time there isn't any third party server to test against.
    Blogger and TypePad implemented this algorithm at one point
    but Blogger has since switched to Basic over HTTPS and
    TypePad has implemented it wrong, by never issuing a 401
    challenge but instead requiring your client to telepathically know that
    their endpoint is expecting WSSE profile="UsernameToken"."""

    def __init__(self, credentials, host, request_uri, headers, response, content, http):
        Authentication.__init__(self, credentials, host, request_uri, headers, response, content, http)

    def request(self, method, request_uri, headers, content):
        """Modify the request headers to add the appropriate
        Authorization header."""
        headers["authorization"] = 'WSSE profile="UsernameToken"'
        iso_now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        cnonce = _cnonce()
        password_digest = _wsse_username_token(cnonce, iso_now, self.credentials[1])
        headers["X-WSSE"] = ('UsernameToken Username="%s", PasswordDigest="%s", ' 'Nonce="%s", Created="%s"') % (
            self.credentials[0],
            password_digest,
            cnonce,
            iso_now,
        )


@model(httplib2.GoogleLoginAuthentication)
class GoogleLoginAuthentication(Authentication):
    def __init__(self, credentials, host, request_uri, headers, response, content, http):
        from urllib.parse import urlencode

        Authentication.__init__(self, credentials, host, request_uri, headers, response, content, http)
        challenge = auth._parse_www_authenticate(response, "www-authenticate")
        service = challenge["googlelogin"].get("service", "xapi")
        # Bloggger actually returns the service in the challenge
        # For the rest we guess based on the URI
        if service == "xapi" and request_uri.find("calendar") > 0:
            service = "cl"
        # No point in guessing Base or Spreadsheet
        # elif request_uri.find("spreadsheets") > 0:
        #    service = "wise"

        auth = dict(Email=credentials[0], Passwd=credentials[1], service=service, source=headers["user-agent"],)
        resp, content = self.http.request(
            "https://www.google.com/accounts/ClientLogin",
            method="POST",
            body=urlencode(auth),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        lines = content.split("\n")
        d = dict([tuple(line.split("=", 1)) for line in lines if line])
        if resp.status == 403:
            self.Auth = ""
        else:
            self.Auth = d["Auth"]

    def request(self, method, request_uri, headers, content):
        """Modify the request headers to add the appropriate
        Authorization header."""
        headers["authorization"] = "GoogleLogin Auth=" + self.Auth


AUTH_SCHEME_CLASSES = {
    "basic": BasicAuthentication,
    "wsse": WsseAuthentication,
    "digest": DigestAuthentication,
    "hmacdigest": HmacDigestAuthentication,
    "googlelogin": GoogleLoginAuthentication,
}

AUTH_SCHEME_ORDER = ["hmacdigest", "googlelogin", "digest", "wsse", "basic"]


@model(httplib2.Credentials)
class Credentials(object):
    def __init__(self):
        self.credentials = []

    def add(self, name, password, domain=""):
        self.credentials.append((domain.lower(), name, password))

    def clear(self):
        self.credentials = []

    def iter(self, domain):
        for (cdomain, name, password) in self.credentials:
            if cdomain == "" or domain == cdomain:
                yield (name, password)


@model(httplib2.KeyCerts)
class KeyCerts(Credentials):
    """Identical to Credentials except that
    name/password are mapped to key/cert."""

    def add(self, key, cert, domain, password):
        self.credentials.append((domain.lower(), key, cert, password))

    def iter(self, domain):
        for (cdomain, key, cert, password) in self.credentials:
            if cdomain == "" or domain == cdomain:
                yield (key, cert, password)


@model(httplib2.AllHosts)
class AllHosts(object):
    pass


@model(httplib2.ProxyInfo)
class ProxyInfo(object):
    """Collect information required to use a proxy."""

    bypass_hosts = ()

    def __init__(
        self, proxy_type, proxy_host, proxy_port, proxy_rdns=True, proxy_user=None, proxy_pass=None, proxy_headers=None,
    ):
        if isinstance(proxy_user, bytes):
            proxy_user = proxy_user.decode()
        if isinstance(proxy_pass, bytes):
            proxy_pass = proxy_pass.decode()
        (
            self.proxy_type,
            self.proxy_host,
            self.proxy_port,
            self.proxy_rdns,
            self.proxy_user,
            self.proxy_pass,
            self.proxy_headers,
        ) = (
            proxy_type,
            proxy_host,
            proxy_port,
            proxy_rdns,
            proxy_user,
            proxy_pass,
            proxy_headers,
        )

    def astuple(self):
        return (
            self.proxy_type,
            self.proxy_host,
            self.proxy_port,
            self.proxy_rdns,
            self.proxy_user,
            self.proxy_pass,
            self.proxy_headers,
        )

    def isgood(self):
        return socks and (self.proxy_host != None) and (self.proxy_port != None)

    def applies_to(self, hostname):
        return not self.bypass_host(hostname)

    def bypass_host(self, hostname):
        """Has this host been excluded from the proxy config"""
        if self.bypass_hosts is AllHosts:
            return True

        hostname = "." + hostname.lstrip(".")
        for skip_name in self.bypass_hosts:
            # *.suffix
            if skip_name.startswith(".") and hostname.endswith(skip_name):
                return True
            # exact match
            if hostname == "." + skip_name:
                return True
        return False

    def __repr__(self):
        return (
            "<ProxyInfo type={p.proxy_type} "
            "host:port={p.proxy_host}:{p.proxy_port} rdns={p.proxy_rdns}"
            + " user={p.proxy_user} headers={p.proxy_headers}>"
        ).format(p=self)


@model(httplib2.Response)
class Response(dict):
    """An object more like email.message than httplib.HTTPResponse."""

    """Is this response from our local cache"""
    fromcache = False
    """HTTP protocol version used by server.

    10 for HTTP/1.0, 11 for HTTP/1.1.
    """
    version = 11

    "Status code returned by server. "
    status = 200
    """Reason phrase returned by server."""
    reason = "Ok"

    previous = None

    def __init__(self, info):
        # info is either an email.message or
        # an httplib.HTTPResponse object.
        if isinstance(info, HTTPResponse):
            for key, value in info.getheaders():
                key = key.lower()
                prev = self.get(key)
                if prev is not None:
                    value = ", ".join((prev, value))
                self[key] = value
            self.status = info.status
            self["status"] = str(self.status)
            self.reason = info.reason
            self.version = info.version
        elif isinstance(info, Message):
            for key, value in list(info.items()):
                self[key.lower()] = value
            self.status = int(self["status"])
        else:
            for key, value in info.items():
                self[key.lower()] = value
            self.status = int(self.get("status", self.status))

    def __getattr__(self, name):
        if name == "dict":
            return self
        else:
            raise AttributeError(name)


def auth_from_challenge(http, host, request_uri, headers, response, content):
    """A generator that creates Authorization objects
       that can be applied to requests.
    """
    # Http._auth_from_challenge, with the Http as http
    challenges = auth._parse_www_authenticate(response, "www-authenticate")
    for cred in http.credentials.iter(host):
        for scheme in AUTH_SCHEME_ORDER:
            if scheme in challenges:
                yield AUTH_SCHEME_CLASSES[scheme](cred, host, request_uri, headers, response, content, http)


# ------------------------------------------------------------------ what the specs say

def free_of(text, characters):
    return not any(character in characters for character in text)

def joined(scheme, authority, path, query, fragment):
    # the uri that parts make, the other way around from parse_uri
    return "".join([
        "" if scheme is None else f"{scheme}:", "" if authority is None else f"//{authority}", path,
        "" if query is None else f"?{query}", "" if fragment is None else f"#{fragment}",
    ])

def has_scheme(uri):
    head = until(uri, ":/?#")
    return head != "" and uri[len(head):len(head) + 1] == ":"

SAFE_CHARACTERS = frozenset(string.ascii_letters + string.digits + "_-.()=!")

def without_scheme(name):
    # the name without a leading scheme:// of word characters
    head, separator, rest = name.partition("://")
    return rest if separator and head and all(c.isalnum() or c == "_" for c in head) else name

PROXY_TYPES = {"socks4": 1, "socks5": 2, "socks": 2}  # socks.PROXY_TYPE_*; any other scheme is HTTP, 3
DEFAULT_PORTS = {"http": 80, "https": 443}

def bypassed(noproxy):
    # the hosts a proxy is bypassed for: all of them for "*", otherwise the names in a comma-separated list
    if noproxy == "*":
        return AllHosts
    return tuple(filter(bool, noproxy.split(","))) if noproxy.strip() else []


# ------------------------------------------------------------------ what Http.request makes of the server's side

def received(status, reason, version, fields):
    # the Response httplib2 makes of what the server sent: fields by lowercase name, a repeated one joined
    response = Response({})
    for name, value in fields.items():
        name = name.lower()
        response[name] = value if name not in response else f"{response[name]}, {value}"
    response.status, response.reason, response.version = status, reason, version
    response["status"] = str(status)
    return response

def status_code_for(error):
    # the (response, content) that force_exception_to_status_code makes of an error instead of raising it
    if isinstance(error, HttpLib2ErrorWithResponse):
        response = error.response
        response.status, response.reason = 500, str(error)
        return response, error.content
    if isinstance(error, timeout):
        content, status, reason = b"Request Timeout", "408", "Request Timeout"
    else:
        content, status, reason = str(error).encode("utf-8"), "400", "Bad Request"
    response = Response({"content-type": "text/plain", "status": status, "content-length": len(content)})
    response.reason = reason
    return response, content


# ------------------------------------------------------------------ the model

@model(httplib2)
class _model_httplib2:
    # the models of its submodules, as httplib2 holds them
    _model_error = _model_error
    _model_iri2uri = _model_iri2uri
    _model_decode = _model_decode

    # the copies of its classes
    Authentication = Authentication
    BasicAuthentication = BasicAuthentication
    DigestAuthentication = DigestAuthentication
    HmacDigestAuthentication = HmacDigestAuthentication
    WsseAuthentication = WsseAuthentication
    GoogleLoginAuthentication = GoogleLoginAuthentication
    Credentials = Credentials
    KeyCerts = KeyCerts
    AllHosts = AllHosts
    ProxyInfo = ProxyInfo
    Response = Response

    @model(httplib2.parse_uri)
    def parse_uri(uri):
        # the parts of RFC 3986's appendix B, each without the characters that start the parts after it
        scheme = magic(str, NoneType)
        authority = magic(str, NoneType)
        path = magic(str)
        query = magic(str, NoneType)
        fragment = magic(str, NoneType)
        constraint(scheme is None or (scheme != "" and free_of(scheme, ":/?#")))
        constraint(authority is None or free_of(authority, "/?#"))
        constraint(free_of(path, "?#"))
        constraint(query is None or free_of(query, "#"))
        constraint(fragment is None or free_of(fragment, "\n"))
        # they make the uri, but for what follows a newline in the fragment, which is dropped
        whole = joined(scheme, authority, path, query, fragment)
        constraint(uri == whole or (fragment is not None and uri.startswith(whole + "\n")))
        # a part takes all it can: a scheme or an authority left out isn't there, and an authority ends the host
        constraint(scheme is not None or not has_scheme(uri))
        constraint(authority is not None or not path.startswith("//"))
        constraint(authority is None or path == "" or path.startswith("/"))
        return scheme, authority, path, query, fragment

    @model(httplib2.urlnorm)
    def urlnorm(uri):
        # an absolute uri's scheme and authority in lowercase, the uri to request ("/" at least) and the uri
        # without its fragment
        uri_scheme, uri_authority, path, query, _ = parse_uri(uri)
        if not uri_scheme or not uri_authority:
            raise RelativeURIError(f"Only absolute URIs are allowed. uri = {uri}")
        scheme = magic(str)
        authority = magic(str)
        request_uri = magic(str)
        defrag_uri = magic(str)
        constraint(scheme == uri_scheme.lower() and authority == uri_authority.lower())
        constraint(request_uri == (path or "/") + (f"?{query}" if query else ""))
        constraint(defrag_uri == f"{scheme}://{authority}{request_uri}")
        return scheme, authority, request_uri, defrag_uri

    @model(httplib2.safename)
    def safename(filename):
        # a cache filename: the name's characters that are safe on any filesystem, cut short, and the md5 of the
        # whole name to tell apart the names cut the same
        if isinstance(filename, bytes):
            name_bytes, filename = filename, filename.decode("utf-8")
        else:
            name_bytes = filename.encode("utf-8")
        safe = magic(str)
        name, comma, digest = safe.rpartition(",")
        constraint(comma == "," and digest == hashlib.md5(name_bytes).hexdigest())
        constraint(name == "".join(c for c in without_scheme(filename) if c in SAFE_CHARACTERS)[:90])
        return safe

    @model(httplib2.has_timeout)
    def has_timeout(timeout):
        # whether a timeout was set: None and socket's default are none
        answer = magic(bool)
        constraint(answer == (timeout is not None and timeout is not _GLOBAL_DEFAULT_TIMEOUT))
        return answer

    @model(httplib2.proxy_info_from_url)
    def proxy_info_from_url(url, method="http", noproxy=None):
        # the proxy of a URL; without noproxy, the hosts it's bypassed for are read from no_proxy or NO_PROXY
        parts = urllib.parse.urlparse(url)
        port = parts.port or DEFAULT_PORTS[method]  # a bad port raises ValueError, a method without one KeyError
        if noproxy is None:
            no_proxy = magic(str, NoneType)
            upper_no_proxy = magic(str, NoneType)
            noproxy = no_proxy if no_proxy is not None else upper_no_proxy if upper_no_proxy is not None else ""
        info = magic(ProxyInfo)
        constraint(info.astuple() == (PROXY_TYPES.get(parts.scheme, 3), parts.hostname, port, True,
                                      parts.username or None, parts.password or None, None))
        constraint(info.bypass_hosts == bypassed(noproxy))
        return info

    @model(httplib2.proxy_info_from_environment)
    def proxy_info_from_environment(method="http"):
        # the proxy of http_proxy or HTTP_PROXY (https_ ones for https), if any; other methods have none
        if method not in ("http", "https"):
            return None
        variable = magic(str, NoneType)
        upper_variable = magic(str, NoneType)
        url = variable if variable is not None else upper_variable
        if not url:
            return None
        return proxy_info_from_url(url, method, noproxy=None)

    @model(httplib2.try_value_or_env)
    def try_value_or_env(to, value, env_key, default=None):
        # the first of value, the variable env_key and the variable in uppercase that converts with to
        variable = magic(str, NoneType)
        upper_variable = magic(str, NoneType)
        constraint(env_key != env_key.upper() or variable == upper_variable)  # one variable read twice
        for candidate in (value, variable, upper_variable):
            if candidate is not None:
                try:
                    return to(candidate)
                except ValueError:
                    pass
        return default

    @model(httplib2.Http)
    class _model_Http:
        @model(httplib2.Http.__init__)
        def __init__(self, cache=None, timeout=None, proxy_info=proxy_info_from_environment, ca_certs=None,
                     disable_ssl_certificate_validation=False, tls_maximum_version=None, tls_minimum_version=None,
                     decode_limit_hard=None, decode_limit_safe=None, decode_limit_ratio=None,
                     decode_limit_chunk=None):
            # an Http with no connections, credentials or certificates yet, and decode limits from the arguments
            # or the environment
            if cache:
                raise RunningUnstatedModel(
                    f"the httplib2 model doesn't model the cache {cache!r}; make Http() without cache= to run it"
                )
            self.proxy_info = proxy_info
            self.ca_certs = ca_certs
            self.disable_ssl_certificate_validation = disable_ssl_certificate_validation
            self.tls_maximum_version = tls_maximum_version
            self.tls_minimum_version = tls_minimum_version
            self.connections = {}
            self.cache = cache
            self.credentials = Credentials()
            self.certificates = KeyCerts()
            self.authorizations = []
            self.follow_redirects = True
            self.redirect_codes = REDIRECT_CODES
            self.optimistic_concurrency_methods = ["PUT", "PATCH"]
            self.safe_methods = list(SAFE_METHODS)
            self.follow_all_redirects = False
            self.ignore_etag = False
            self.force_exception_to_status_code = False
            self.timeout = timeout
            self.forward_authorization_headers = False
            limit_kwargs = dict(
                hard_limit=try_value_or_env(int, decode_limit_hard, "httplib2_decode_limit_hard"),
                safe_limit=try_value_or_env(int, decode_limit_safe, "httplib2_decode_limit_safe"),
                ratio=try_value_or_env(float, decode_limit_ratio, "httplib2_decode_limit_ratio"),
                chunk_size=try_value_or_env(int, decode_limit_chunk, "httplib2_decode_limit_chunk"),
            )
            self.limit_kwargs = {k: v for k, v in limit_kwargs.items() if v is not None}

        @model(httplib2.Http.request)
        def request(self, uri, method="GET", body=None, headers=None, redirections=DEFAULT_MAX_REDIRECTS,
                    connection_type=None):
            if self.cache:
                raise RunningUnstatedModel(
                    f"the httplib2 model doesn't model the cache of {self!r}; make Http() without cache= to run it"
                )

            def exchange(method, request_uri, body, headers):
                # one round trip, as Http._conn_request makes it: each attempt draws its error, None when it goes
                # through, and a retried one draws again
                attempt, retried_bad_status_line = 0, False
                while True:
                    attempt += 1
                    error = magic(NoneType, OSError, HTTPException, ProxiesUnavailableError)
                    if error is None:
                        break
                    if isinstance(error, (BadStatusLine, ResponseNotReady)):
                        # a stale connection: the first bad status line is retried, whatever the attempts left
                        if attempt == 1 and not retried_bad_status_line:
                            attempt, retried_bad_status_line = 0, True
                            continue
                        raise error
                    if isinstance(error, gaierror):
                        raise ServerNotFoundError(f"Unable to find the server at {host}")
                    transient = _errno_from_exception(error) in (ENETUNREACH, EADDRNOTAVAIL)
                    if isinstance(error, OSError) and transient and attempt < RETRIES:
                        continue
                    raise error

                status = magic(int)
                constraint(101 <= status <= 999)  # http.client skips 100 Continue and refuses the rest
                reason = magic(str)
                version = magic(int)
                constraint(version in (10, 11))  # HTTP/1.0 (or 0.9) and HTTP/1.1
                response_headers = magic(dict)
                constraint(all(isinstance(name, str) and isinstance(value, str)
                               for name, value in response_headers.items()))
                response = received(status, reason, version, response_headers)
                if method == "HEAD":
                    return response, b""
                if status in (204, 304) or status < 200:
                    content = b""  # http.client reads no body for these
                else:
                    content = magic(bytes)
                return response, _decompressContent(response, content, self.limit_kwargs)

            try:
                headers = {} if headers is None else _normalize_headers(headers)
                headers.setdefault("user-agent", f"Python-httplib2/{HTTPLIB2_VERSION} (gzip)")
                uri = iri2uri(uri).replace(" ", "%20").replace("\r", "%0D").replace("\n", "%0A")
                scheme, authority, request_uri, _ = urlnorm(uri)
                if not connection_type and scheme not in CONNECTION_SCHEMES:
                    raise KeyError(scheme)  # httplib2 has no connection class for it
                # as HTTPConnection(authority) takes its host: a bad port or a control character raises InvalidURL
                host, _ = _get_hostport(authority, None)
                _validate_host(host)
                if "range" not in headers and "accept-encoding" not in headers:
                    headers["accept-encoding"] = "gzip, deflate"

                if "only-if-cached" in _parse_cache_control(headers):
                    # nothing is cached, so nothing is fetched
                    response, content = Response({"status": "504"}), b""
                else:
                    # the authorization that worked before for this uri goes first; a stale digest is answered again
                    auths = [(auth.depth(request_uri), auth) for auth in self.authorizations
                             if auth.inscope(authority, request_uri)]
                    auth = auths and sorted(auths)[0][1] or None
                    if auth:
                        auth.request(method, request_uri, headers, body)
                    response, content = exchange(method, request_uri, body, headers)
                    if auth and auth.response(response, body):
                        auth.request(method, request_uri, headers, body)
                        response, content = exchange(method, request_uri, body, headers)
                        response._stale_digest = 1

                    # a challenge is answered with each credential that fits it, until one is let in
                    if response.status == 401:
                        for authorization in auth_from_challenge(self, authority, request_uri, headers, response,
                                                                 content):
                            authorization.request(method, request_uri, headers, body)
                            response, content = exchange(method, request_uri, body, headers)
                            if response.status != 401:
                                self.authorizations.append(authorization)
                                authorization.response(response, body)
                                break

                    if self.follow_all_redirects or method in self.safe_methods or response.status in (303, 308):
                        if self.follow_redirects and response.status in self.redirect_codes:
                            if not redirections:
                                raise RedirectLimit(
                                    "Redirected more times than redirection_limit allows.", response, content
                                )
                            if "location" not in response and response.status != 300:
                                raise RedirectMissingLocation(
                                    "Redirected but the response is missing a Location: header.", response, content
                                )
                            if "location" in response and parse_uri(response["location"])[1] is None:
                                response["location"] = urllib.parse.urljoin(uri, response["location"])
                            if response.status == 308 or (response.status == 301 and method in self.safe_methods):
                                response["-x-permanent-redirect-url"] = response["location"]
                                response.setdefault("content-location", uri)
                            for validator in ("if-none-match", "if-modified-since"):
                                headers.pop(validator, None)
                            if not self.forward_authorization_headers:
                                headers.pop("authorization", None)
                            if "location" in response:
                                previous = copy.deepcopy(response)
                                previous.setdefault("content-location", uri)
                                redirect_method = method
                                if response.status in (302, 303):
                                    redirect_method, body = "GET", None
                                response, content = self.request(
                                    response["location"], method=redirect_method, body=body, headers=headers,
                                    redirections=redirections - 1,
                                )
                                response.previous = previous
                        elif response.status in (200, 203) and method in self.safe_methods:
                            response.setdefault("content-location", uri)
            except Exception as error:
                if not self.force_exception_to_status_code:
                    raise
                response, content = status_code_for(error)
            return response, content

        @model(httplib2.Http.add_credentials)
        def add_credentials(self, name, password, domain=""):
            # from now on a challenge from the domain, or from any when it's "", is answered with these
            self.credentials.add(name, password, domain)

        @model(httplib2.Http.add_certificate)
        def add_certificate(self, key, cert, domain, password=None):
            # from now on HTTPS connections to the domain present this key and certificate
            self.certificates.add(key, cert, domain, password)

        @model(httplib2.Http.clear_credentials)
        def clear_credentials(self):
            # the names and passwords are forgotten, with the authorizations they got
            self.credentials.clear()
            self.authorizations = []

        @model(httplib2.Http.close)
        def close(self):
            # the model opens no connections, so dropping them closes them; certificates and credentials go too
            self.connections = {}
            self.certificates.clear()
            self.clear_credentials()


models = [_model_httplib2, *stdlib_models]
