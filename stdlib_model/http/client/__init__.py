# the model of http.client: copies of its exceptions, which stand in for them in a run, and of how an
# HTTPConnection takes its host apart. Connections and responses aren't modelled: HTTPResponse is named only,
# for the isinstance() in httplib2's Response, as no model makes one
# (the copies are from CPython 3.10, Python Software Foundation License)

import http.client

from PyMagic import model, named

HTTP_PORT = 80
HTTPS_PORT = 443

_MAXLINE = 65536


# ------------------------------------------------------------------ copied from http.client

@model(http.client.HTTPException)
class HTTPException(Exception):
    # Subclasses that define an __init__ must call Exception.__init__
    # or define self.args.  Otherwise, str() will fail.
    pass

@model(http.client.NotConnected)
class NotConnected(HTTPException):
    pass

@model(http.client.InvalidURL)
class InvalidURL(HTTPException):
    pass

@model(http.client.UnknownProtocol)
class UnknownProtocol(HTTPException):
    def __init__(self, version):
        self.args = version,
        self.version = version

@model(http.client.UnknownTransferEncoding)
class UnknownTransferEncoding(HTTPException):
    pass

@model(http.client.UnimplementedFileMode)
class UnimplementedFileMode(HTTPException):
    pass

@model(http.client.IncompleteRead)
class IncompleteRead(HTTPException):
    def __init__(self, partial, expected=None):
        self.args = partial,
        self.partial = partial
        self.expected = expected
    def __repr__(self):
        if self.expected is not None:
            e = ', %i more expected' % self.expected
        else:
            e = ''
        return '%s(%i bytes read%s)' % (self.__class__.__name__,
                                        len(self.partial), e)
    __str__ = object.__str__

@model(http.client.ImproperConnectionState)
class ImproperConnectionState(HTTPException):
    pass

@model(http.client.CannotSendRequest)
class CannotSendRequest(ImproperConnectionState):
    pass

@model(http.client.CannotSendHeader)
class CannotSendHeader(ImproperConnectionState):
    pass

@model(http.client.ResponseNotReady)
class ResponseNotReady(ImproperConnectionState):
    pass

@model(http.client.BadStatusLine)
class BadStatusLine(HTTPException):
    def __init__(self, line):
        if not line:
            line = repr(line)
        self.args = line,
        self.line = line

@model(http.client.LineTooLong)
class LineTooLong(HTTPException):
    def __init__(self, line_type):
        HTTPException.__init__(self, "got more than %d bytes when reading %s"
                                     % (_MAXLINE, line_type))

@model(http.client.RemoteDisconnected)
class RemoteDisconnected(ConnectionResetError, BadStatusLine):
    def __init__(self, *pos, **kw):
        BadStatusLine.__init__(self, "")
        ConnectionResetError.__init__(self, *pos, **kw)

# for backwards compatibility
error = HTTPException


def _get_hostport(host, port, default_port=HTTP_PORT):
    # HTTPConnection._get_hostport, with the connection's default port as default_port
    if port is None:
        i = host.rfind(':')
        j = host.rfind(']')         # ipv6 addresses have [...]
        if i > j:
            try:
                port = int(host[i+1:])
            except ValueError:
                if host[i+1:] == "": # http://foo.com:/ == http://foo.com/
                    port = default_port
                else:
                    raise InvalidURL("nonnumeric port: '%s'" % host[i+1:])
            host = host[:i]
        else:
            port = default_port
        if host and host[0] == '[' and host[-1] == ']':
            host = host[1:-1]

    return (host, port)


# the characters _contains_disallowed_url_pchar_re matches: [\x00-\x20\x7f]
_disallowed_url_pchars = frozenset(map(chr, [*range(0x21), 0x7f]))

def _validate_host(host):
    """Validate a host so it doesn't contain control characters."""
    # HTTPConnection._validate_host, finding the first character it refuses without a regular expression
    # Prevent CVE-2019-18348.
    match = next((c for c in host if c in _disallowed_url_pchars), None)
    if match:
        raise InvalidURL(f"URL can't contain control characters. {host!r} "
                         f"(found at least {match!r})")


HTTPResponse = named(http.client.HTTPResponse)


# ------------------------------------------------------------------ the model

@model(http.client)
class _model_client:
    # the copies of its exceptions
    HTTPException = HTTPException
    NotConnected = NotConnected
    InvalidURL = InvalidURL
    UnknownProtocol = UnknownProtocol
    UnknownTransferEncoding = UnknownTransferEncoding
    UnimplementedFileMode = UnimplementedFileMode
    IncompleteRead = IncompleteRead
    ImproperConnectionState = ImproperConnectionState
    CannotSendRequest = CannotSendRequest
    CannotSendHeader = CannotSendHeader
    ResponseNotReady = ResponseNotReady
    BadStatusLine = BadStatusLine
    LineTooLong = LineTooLong
    RemoteDisconnected = RemoteDisconnected
