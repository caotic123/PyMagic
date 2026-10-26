import http.client

import pytest

from stdlib_model.http.client import _get_hostport, _validate_host
from stdlib_model.testing import modelled, outcome

AUTHORITIES = ["example.com", "example.com:8080", "example.com:", "example.com:abc", "[::1]", "[::1]:8080", "[::1",
               "::1", "", ":80", "host:+80", "host: 80", "a b", "a\x00b", "a\x7fb", "hé", "host:٨٠",
               "user:pass@host", "host:99999"]

def split(authority):
    host, port = _get_hostport(authority, None)
    _validate_host(host)
    return host, port

@pytest.mark.parametrize("authority", AUTHORITIES, ids=repr)
def test_takes_the_host_apart_as_http_connection(authority):
    connection = lambda: (lambda made: (made.host, made.port))(http.client.HTTPConnection(authority))
    assert modelled(lambda: split(authority)) == outcome(connection)

EXCEPTIONS = {
    "incomplete read": lambda: (repr(http.client.IncompleteRead(b"ab", 3)), str(http.client.IncompleteRead(b"ab"))),
    "unknown protocol": lambda: http.client.UnknownProtocol("HTTP/9").args,
    "bad status line": lambda: (http.client.BadStatusLine("").line, http.client.BadStatusLine("x").args),
    "line too long": lambda: str(http.client.LineTooLong("header line")),
    "remote disconnected": lambda: (str(http.client.RemoteDisconnected("gone")), http.client.RemoteDisconnected().line),
    "kinds": lambda: (issubclass(http.client.RemoteDisconnected, ConnectionResetError),
                      issubclass(http.client.ResponseNotReady, http.client.ImproperConnectionState),
                      http.client.error is http.client.HTTPException),
}

@pytest.mark.parametrize("call", EXCEPTIONS.values(), ids=EXCEPTIONS.keys())
def test_exceptions_as_http_client(call):
    assert modelled(call) == outcome(call)

def test_exceptions_are_the_copies_in_a_run_and_leave_it_as_the_originals():
    from stdlib_model.http import client
    assert modelled(lambda: http.client.BadStatusLine is client.BadStatusLine) == ("returned", True)
    def raises():
        raise http.client.RemoteDisconnected("gone")
    assert modelled(raises) == outcome(raises)
