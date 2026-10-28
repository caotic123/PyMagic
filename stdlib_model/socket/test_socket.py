import errno
import http.client
import inspect
import io
import random
import socket
import subprocess
import sys
import _socket

import pytest

from PyMagic import BrokenVariant, Compilation, RunningUnstatedModel, compile_models, install, run_model
from PyMagic.compile import write_package
from stdlib_model import models, socket as model_socket
from stdlib_model.magics import network, real_network
from stdlib_model.testing import modelled, modelled_each, outcome

def test_gaierror_is_the_copy_in_a_run_and_leaves_it_as_the_original():
    assert modelled(lambda: socket.gaierror is model_socket.gaierror) == ("returned", True)
    def raises():
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
    assert modelled(raises) == outcome(raises)

def test_names_are_sockets():
    assert model_socket.timeout is socket.timeout
    assert model_socket._GLOBAL_DEFAULT_TIMEOUT is socket._GLOBAL_DEFAULT_TIMEOUT


# ------------------------------------------------------------------ on Linux, and on the models over it

# the originals, kept where a run doesn't put the models in their place
ORIGINALS = (socket.socket, socket.getaddrinfo, _socket.socket)

def on_both(scenarios):
    # what each scenario gives on the kernel, and on the models with the kernel answering what they draw
    plain = [outcome(scenario) for scenario in scenarios]
    def on_the_models():
        assert (socket.socket, socket.getaddrinfo, _socket.socket) != ORIGINALS
        assert socket.socket is model_socket.socket
        return [outcome(scenario) for scenario in scenarios]
    return plain, run_model(models, real_network())(on_the_models)

def tcp():
    return socket.socket(socket.AF_INET, socket.SOCK_STREAM)

def udp():
    return socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

def on(make, *calls):
    # the outcome of each call, in turn, on a socket make makes, which is closed then
    def scenario():
        sock = make()
        try:
            return [outcome(lambda: call(sock)) for call in calls]
        finally:
            sock.close()
    return scenario

def closed(make):
    def made():
        sock = make()
        sock.close()
        return sock
    return made

def connected(*calls):
    # the outcome of each call, in turn, on a client and the server's end of its connection over the loopback
    def scenario():
        server = tcp()
        server.bind(("127.0.0.1", 0))
        server.listen()
        client = tcp()
        client.connect(server.getsockname())
        accepted, _ = server.accept()
        server.close()
        try:
            return [outcome(lambda: call(client, accepted)) for call in calls]
        finally:
            client.close()
            accepted.close()
    return scenario

def received(sock, size):
    # size bytes from a stream, as many recv() as it takes
    data = b""
    while len(data) < size:
        data += sock.recv(size - len(data))
    return data

def into(sock, size, call="recv_into"):
    # what recv_into() or recvfrom_into() gives with a buffer of size bytes, and the buffer
    buffer = bytearray(size)
    return getattr(sock, call)(buffer), bytes(buffer)

def paired(*calls):
    def scenario():
        first, second = socket.socketpair()
        try:
            return [outcome(lambda: call(first, second)) for call in calls]
        finally:
            first.close()
            second.close()
    return scenario

S = socket
ANYWHERE, NOWHERE = ("127.0.0.1", 9), ("127.0.0.1", 1)

SCENARIOS = {
    "fresh stream": [on(tcp, *[call]) for call in [
        lambda s: s.recv(1), lambda s: s.recv(0), lambda s: s.send(b"x"), lambda s: s.send(b""),
        lambda s: s.sendall(b"x"), lambda s: s.sendto(b"x", ANYWHERE), lambda s: s.recvfrom(1),
        lambda s: s.getpeername(), lambda s: s.getsockname(), lambda s: s.shutdown(S.SHUT_RDWR), lambda s: s.accept(),
        lambda s: s.gettimeout(), lambda s: s.getblocking(), lambda s: s.getsockopt(S.SOL_SOCKET, S.SO_TYPE),
        lambda s: s.getsockopt(S.SOL_SOCKET, S.SO_ERROR), lambda s: s.getsockopt(S.SOL_SOCKET, S.SO_ACCEPTCONN),
        lambda s: s.getsockopt(S.SOL_SOCKET, S.SO_REUSEADDR), lambda s: s.getsockopt(S.SOL_SOCKET, S.SO_TYPE, 4),
        lambda s: s.getsockopt(S.SOL_SOCKET, S.SO_TYPE, 2000), lambda s: s.getsockopt(S.SOL_SOCKET, 9999),
        lambda s: s.setsockopt(S.SOL_SOCKET, 9999, 1), lambda s: s.setsockopt(S.SOL_SOCKET, S.SO_REUSEADDR, "x"),
        lambda s: (s.setsockopt(S.SOL_SOCKET, S.SO_REUSEADDR, 5), s.getsockopt(S.SOL_SOCKET, S.SO_REUSEADDR)),
        lambda s: s.connect_ex(NOWHERE), lambda s: s.connect(NOWHERE), lambda s: (s.listen(), s.getsockname()[0]),
        lambda s: (s.listen(), s.connect(NOWHERE)), lambda s: (s.listen(), s.send(b"x")),
        lambda s: (s.listen(), s.recv(1)), lambda s: (s.listen(), s.listen(3)), lambda s: (s.listen(), s.bind(("", 0))),
        lambda s: (s.listen(), s.shutdown(S.SHUT_RD)), lambda s: (s.settimeout(0), s.listen(), s.accept()),
        lambda s: (s.settimeout(0.01), s.listen(), s.accept()), lambda s: (s.bind(("", 0)), s.bind(("", 0))),
        lambda s: (s.bind(("127.0.0.1", 0)), s.getsockname()[0]), lambda s: s.bind(("", 70000)),
        lambda s: s.bind(("", -1)), lambda s: s.bind("x"), lambda s: s.bind(("x",)), lambda s: s.bind((1, 2)),
        lambda s: s.bind(("", "a")), lambda s: s.bind(("", 1.5)), lambda s: s.bind(("1.2.3", 0)),
        lambda s: s.bind(("256.1.1.1", 0)), lambda s: s.bind(("localhost", 0)), lambda s: s.bind(("a\0b", 0)),
        lambda s: s.bind(("<broadcast>", 0)), lambda s: s.connect(("", 70000)), lambda s: s.sendto(b"x", ("", 70000)),
        lambda s: s.settimeout(-1), lambda s: s.settimeout("x"), lambda s: s.settimeout(float("nan")),
        lambda s: (s.settimeout(True), s.gettimeout()), lambda s: s.settimeout(1e300), lambda s: s.settimeout(2**62),
        lambda s: (s.settimeout(1 / 3), s.gettimeout(), s.timeout), lambda s: (s.settimeout(1e-10), s.gettimeout()),
        lambda s: (s.setblocking(0), s.gettimeout(), s.getblocking()), lambda s: s.setblocking(None),
        lambda s: s.setblocking(2.5), lambda s: s.recv(-1), lambda s: s.recv("x"), lambda s: s.recv(1.0),
        lambda s: s.recv(2**63), lambda s: s.send("x"), lambda s: s.send(1), lambda s: s.sendto(b"x"),
        lambda s: s.sendto(b"x", 0, 1, 2), lambda s: s.recv_into(bytearray(2), 3), lambda s: s.recv_into(bytearray(2), -1),
        lambda s: s.recv_into(b"ab"), lambda s: s.recvfrom_into(bytearray(2), 3), lambda s: s.shutdown(5),
        lambda s: (s.listen(-5), s.getsockopt(S.SOL_SOCKET, S.SO_ACCEPTCONN)), lambda s: s.get_inheritable(),
        lambda s: (s.set_inheritable(True), s.get_inheritable()), lambda s: (s.detach() >= 0, s.fileno(), s.recv(1)),
        lambda s: (s.family, s.type, s.proto), lambda s: repr(s).split(" fd=")[1].split(", ", 1)[1],
        lambda s: s.makefile("x"), lambda s: s.makefile("r", 0), lambda s: s.__getstate__(),
        lambda s: s.sendfile(io.BytesIO(b"x")), lambda s: (s.settimeout(0), s.sendfile(io.BytesIO(b"x"))),
    ]],
    "closed stream": [on(closed(tcp), *[call]) for call in [
        lambda s: s.recv(1), lambda s: s.recv(-1), lambda s: s.recv(0), lambda s: s.send(b"x"), lambda s: s.fileno(),
        lambda s: s.close(), lambda s: s.settimeout(1), lambda s: (outcome(lambda: s.settimeout(3)), s.gettimeout()),
        lambda s: s.setblocking(False), lambda s: s.getblocking(), lambda s: s.getsockname(),
        lambda s: s.getpeername(), lambda s: s.bind(("", 0)), lambda s: s.bind("x"), lambda s: s.connect(NOWHERE),
        lambda s: s.connect_ex(NOWHERE), lambda s: s.connect_ex(("", 70000)), lambda s: s.listen(),
        lambda s: s.accept(), lambda s: s.shutdown(0), lambda s: s.shutdown(5),
        lambda s: s.getsockopt(S.SOL_SOCKET, S.SO_TYPE), lambda s: s.setsockopt(S.SOL_SOCKET, S.SO_REUSEADDR, 1),
        lambda s: (s.family, s.type, s.proto), lambda s: s.timeout, lambda s: s.detach(), lambda s: s.get_inheritable(),
        lambda s: repr(s.makefile()), lambda s: s.sendall(b"x"), lambda s: s.sendall(b""), lambda s: repr(s),
        lambda s: s.recv_into(bytearray(1)), lambda s: s.recvfrom(1),
    ]],
    "connected stream": [connected(*[call]) for call in [
        lambda c, a: c.connect(a.getsockname()), lambda c, a: c.connect_ex(a.getsockname()), lambda c, a: c.listen(),
        lambda c, a: c.bind(("", 0)), lambda c, a: (a.close(), c.recv(5), c.recv(5)),
        lambda c, a: (a.close(), c.send(b"x"), outcome(lambda: c.send(b"y")), outcome(lambda: c.recv(1))),
        lambda c, a: (c.shutdown(S.SHUT_WR), c.send(b"x")), lambda c, a: (c.shutdown(S.SHUT_WR), c.sendall(b"")),
        lambda c, a: (c.shutdown(S.SHUT_WR), c.send(b"")), lambda c, a: (c.shutdown(S.SHUT_WR), a.recv(1)),
        lambda c, a: (c.shutdown(S.SHUT_RD), c.recv(1)), lambda c, a: (c.shutdown(S.SHUT_RD), a.send(b"hi"), c.recv(5)),
        lambda c, a: (c.shutdown(S.SHUT_RDWR), c.shutdown(S.SHUT_RDWR)), lambda c, a: (c.settimeout(0), c.recv(1)),
        lambda c, a: (c.settimeout(0.01), c.recv(1)), lambda c, a: c.recv(1, S.MSG_DONTWAIT),
        lambda c, a: (a.send(b"hello"), c.recv(3, S.MSG_PEEK), c.recv(10)), lambda c, a: c.recv(0),
        lambda c, a: (a.send(b"hi"), c.recvfrom(5)), lambda c, a: (c.sendto(b"x", ANYWHERE), a.recv(5)),
        lambda c, a: c.getpeername() == a.getsockname(), lambda c, a: a.getpeername() == c.getsockname(),
        lambda c, a: c.getsockname()[0], lambda c, a: a.gettimeout(), lambda c, a: a.get_inheritable(),
        lambda c, a: c.getsockopt(S.SOL_SOCKET, S.SO_ERROR), lambda c, a: c.getsockopt(S.SOL_SOCKET, S.SO_ACCEPTCONN),
        lambda c, a: (c.sendall(b"0123456789" * 1000), received(a, 10000) == b"0123456789" * 1000),
        lambda c, a: (a.sendall(b"abcdef"), into(c, 3), into(c, 5, "recvfrom_into")),
        lambda c, a: (a.sendall(b"line one\nline two\n"), a.close(), c.makefile("rb").readlines()),
        lambda c, a: (c.makefile("wb", buffering=0).write(b"raw"), a.recv(3)),
        lambda c, a: (c.sendfile(io.BytesIO(b"a file's bytes"), 2, 5), a.recv(10)),
        lambda c, a: (c.settimeout(0.01), c.makefile("rb").read(1)),
    ]],
    "datagrams": [on(udp, *[call]) for call in [
        lambda s: s.send(b"x"), lambda s: s.sendall(b"x"), lambda s: (s.settimeout(0), s.recv(1)),
        lambda s: (s.sendto(b"x", ANYWHERE), s.getsockname()[0]), lambda s: s.getpeername(), lambda s: s.listen(),
        lambda s: s.accept(), lambda s: (s.connect(ANYWHERE), s.getpeername()),
        lambda s: (s.connect(ANYWHERE), s.sendto(b"x", ("127.0.0.1", 10))),
        lambda s: (s.connect(ANYWHERE), s.connect(("127.0.0.1", 10)), s.getpeername()),
        lambda s: s.shutdown(S.SHUT_RDWR), lambda s: (s.connect(ANYWHERE), s.shutdown(S.SHUT_WR), s.send(b"x")),
        lambda s: s.sendto(b"x" * 70000, ANYWHERE), lambda s: (s.sendto(b"x" * 65507, ANYWHERE),
                                                               outcome(lambda: s.sendto(b"x" * 65508, ANYWHERE))),
        lambda s: s.sendto(b"x", 0), lambda s: (s.bind(("127.0.0.1", 0)), s.sendto(b"hello", s.getsockname()),
                                               s.recvfrom(3)[0], outcome(lambda: (s.settimeout(0), s.recv(5)))),
        lambda s: (s.bind(("127.0.0.1", 0)), s.sendto(b"twice", s.getsockname()), s.recv(9, S.MSG_PEEK), s.recv(9)),
        lambda s: (s.bind(("127.0.0.1", 0)), s.settimeout(0.01), s.recvfrom(1)),
        lambda s: (s.bind(("127.0.0.1", 0)), s.sendto(b"in", s.getsockname()), s.recvfrom_into(bytearray(1))[0]),
        lambda s: (s.getsockopt(S.SOL_SOCKET, S.SO_TYPE), s.getsockopt(S.SOL_SOCKET, S.SO_PROTOCOL)),
    ]],
    "made": [
        lambda: socket.socket(12345), lambda: socket.socket(S.AF_INET, 99), lambda: socket.socket(S.AF_INET, S.SOCK_STREAM, 99),
        lambda: socket.socket("x"), lambda: socket.socket(2**40), lambda: socket.socket(fileno=999999),
        lambda: socket.socket(fileno=-1), lambda: socket.socket(fileno=1.5), lambda: socket.setdefaulttimeout(-1),
        lambda: socket.setdefaulttimeout("x"), lambda: socket.getdefaulttimeout(),
        on(lambda: socket.socket(S.AF_INET, S.SOCK_STREAM | S.SOCK_NONBLOCK), lambda s: (s.gettimeout(), s.type)),
        on(lambda: socket.socket(S.AF_INET, S.SOCK_STREAM | S.SOCK_CLOEXEC), lambda s: s.type),
        on(lambda: socket.socket(S.AF_INET6), lambda s: (s.family, s.proto, s.getsockname())),
        on(lambda: socket.socket(S.AF_INET, S.SOCK_STREAM, 6), lambda s: (s.proto, s.getsockopt(S.SOL_SOCKET, S.SO_PROTOCOL))),
        on(lambda: socket.socket(S.AF_UNIX), lambda s: s.getsockname(), lambda s: s.bind("x" * 200), lambda s: s.bind(5),
           lambda s: s.connect("/nonexistent/x"), lambda s: s.bind(b"\0" + b"x" * 108)),
        on(lambda: socket.socket(S.AF_INET6), lambda s: s.bind(("::1",)), lambda s: s.bind(("::1", 0, 1 << 20)),
           lambda s: s.bind(("::1", 0, 0, 0, 0)), lambda s: s.bind("x"), lambda s: s.bind(("<broadcast>", 0)),
           lambda s: (s.bind(("::1", 0)), s.getsockname()[0])),
        on(lambda: socket.socket(fileno=socket.socket().detach()), lambda s: (s.family, s.type, s.proto)),
        lambda: socket.socketpair(S.AF_INET), lambda: socket.close(999999),
        paired(lambda a, b: (a.family, a.type, a.getpeername(), a.getsockname())),
        paired(lambda a, b: (b.sendall(b"ab"), b.close(), a.recv(5), a.recv(5))),
        paired(lambda a, b: (b.close(), a.send(b"x"))), paired(lambda a, b: (a.settimeout(0), a.recv(1))),
        paired(lambda a, b: (b.shutdown(S.SHUT_WR), a.recv(1), a.send(b"ok"), b.recv(2))),
        lambda: (lambda server: (server.getsockname()[0], server.getsockopt(S.SOL_SOCKET, S.SO_REUSEADDR),
                                 server.getsockopt(S.SOL_SOCKET, S.SO_ACCEPTCONN), server.close()))(
            socket.create_server(("127.0.0.1", 0))),
        lambda: socket.create_server(("127.0.0.1", 0), family=S.AF_INET, dualstack_ipv6=True),
        lambda: socket.create_server(("256.1.1.1", 0)), lambda: socket.has_dualstack_ipv6(),
        lambda: socket.create_connection(NOWHERE), lambda: socket.create_connection(("localhost", 1)),
        lambda: socket.create_connection(("127.0.0.1", 1), source_address=("127.0.0.1", 0)),
        lambda: socket.create_connection(("127.0.0.1", 1), timeout=-1),
    ],
}

@pytest.mark.parametrize("group", SCENARIOS)
def test_sockets_do_on_the_models_what_they_do_on_linux(group):
    for number, (plain, over_it) in enumerate(zip(*on_both(SCENARIOS[group]))):
        assert over_it == plain, (group, number)

def test_names_resolve_on_the_models_as_on_linux():
    scenarios = [
        lambda: socket.getaddrinfo("127.0.0.1", 80), lambda: socket.getaddrinfo(None, 80, S.AF_INET, S.SOCK_STREAM),
        lambda: socket.getaddrinfo(None, 80, S.AF_INET, S.SOCK_STREAM, 0, S.AI_PASSIVE),
        lambda: socket.getaddrinfo("", 80), lambda: socket.getaddrinfo(None, None), lambda: socket.getaddrinfo(1, 80),
        lambda: socket.getaddrinfo(None, 1.5), lambda: socket.getaddrinfo(None, True),
        lambda: socket.getaddrinfo("::1", 80, S.AF_INET), lambda: socket.getaddrinfo("::1", "80", S.AF_INET6),
        lambda: socket.getaddrinfo("localhost", b"80", S.AF_INET, S.SOCK_STREAM),
        lambda: socket.getaddrinfo("localhost", "http", S.AF_INET, S.SOCK_STREAM), lambda: socket.gethostbyname("127.0.0.1"),
        lambda: socket.gethostbyname(""), lambda: socket.gethostbyname("<broadcast>"), lambda: socket.gethostbyname(1),
        lambda: socket.gethostbyname("x\0"), lambda: socket.gethostbyname("localhost"),
        lambda: socket.gethostbyname_ex("127.0.0.1"), lambda: socket.gethostbyname_ex(b"127.0.0.1"),
        lambda: socket.gethostname(),
        lambda: socket.getnameinfo(("nonnumeric.invalid", 1), 0), lambda: socket.getnameinfo([], 0),
        lambda: socket.getnameinfo((b"1", 1), 0), lambda: socket.getnameinfo(("127.0.0.1", 1, 0), 0),
        lambda: socket.getnameinfo(("127.0.0.1", 80), S.NI_NUMERICHOST | S.NI_NUMERICSERV),
        lambda: socket.getservbyname("http", "tcp"), lambda: socket.getservbyname("nope"),
        lambda: socket.getservbyname(b"http"), lambda: socket.getservbyport(70000), lambda: socket.getservbyport(80),
        lambda: socket.getservbyport(1, "nope"), lambda: socket.getprotobyname("tcp"),
        lambda: socket.getprotobyname("nope"),
    ]
    plain, over_it = on_both(scenarios)
    for number, (theirs, ours) in enumerate(zip(plain, over_it)):
        assert ours == theirs, number


# ------------------------------------------------------------------ glibc's parsing and byte order

def test_byte_order_and_arguments_are_as_glibc_has_them():
    calls = [lambda value=value, convert=convert: convert(value)
             for convert in (S.htons, S.ntohs, S.htonl, S.ntohl)
             for value in (0, 1, 0x1234, 0xFFFF, 0x10000, 2**31, 2**32 - 1, 2**32, 2**40, 2**64, -1, 1.0, True, "1")]
    assert modelled_each(calls) == [outcome(call) for call in calls]

def test_ipv4_addresses_read_and_written_as_glibc_does():
    pick = random.Random(7)
    texts = ["1.2.3.4", "1.2.3.4 junk", "1", "0x7f.1", "1.2.3.4.5", "256.1.1.1", "1.2.3.0400", "", "09", "0x", " 1",
             "1.", "4294967295", "4294967296", "0377.0xff.255.255", "1.2.65535", "1.16777215", "01.2.3.4", "1.2.3.4\0"]
    texts += ["".join(pick.choice("0123456789xX.af ") for _ in range(pick.randrange(1, 14))) for _ in range(800)]
    calls = [lambda text=text, convert=convert: convert(*args, text)
             for text in texts for convert, *args in ((S.inet_aton,), (S.inet_pton, S.AF_INET))]
    calls += [lambda: S.inet_aton(b"1.2.3.4"), lambda: S.inet_pton(S.AF_INET, b"1.2.3.4"), lambda: S.inet_pton(99, "x"),
              lambda: S.inet_ntoa(b"abc"), lambda: S.inet_ntoa("abcd"), lambda: S.inet_ntoa(bytearray(b"\1\2\3\4")),
              lambda: S.inet_ntop(S.AF_INET, b"abc"), lambda: S.inet_ntop(99, b"abcd")]
    assert modelled_each(calls) == [outcome(call) for call in calls]

def test_ipv6_addresses_read_and_written_as_glibc_does():
    pick = random.Random(11)
    packed = [bytes(16), bytes(15) + b"\1", b"\0" * 10 + b"\xff\xff\1\2\3\4", b"\0" * 12 + b"\1\2\3\4",
              b"\0" * 10 + b"\xff\xfe\1\2\3\4", b"\x20\x01\x0d\xb8" + bytes(12)]
    for _ in range(400):
        words = [pick.choice([0, 0, 0, 1, 0xFFFF, pick.randrange(0x10000)]) for _ in range(8)]
        packed.append(b"".join(word.to_bytes(2, "big") for word in words))
    texts = [S.inet_ntop(S.AF_INET6, address) for address in packed]
    texts += ["::", ":", ":::", "1::2::3", "::ffff:1.2.3.4", "1:2:3:4:5:6:1.2.3.4", "1:2:3:4:5:6:7:1.2.3.4", "1::",
              "::1.2.3", "12345::", "1:2:3:4:5:6:7:8:9", "1:2:3:4:5:6:7::", "::1:2:3:4:5:6:7", "a:b", "fe80::1%eth0"]
    texts += ["".join(pick.choice("0123456789abcdefABCDEF:.") for _ in range(pick.randrange(1, 20))) for _ in range(800)]
    calls = [lambda address=address: S.inet_ntop(S.AF_INET6, address) for address in packed]
    calls += [lambda text=text: S.inet_pton(S.AF_INET6, text) for text in texts]
    assert modelled_each(calls) == [outcome(call) for call in calls]


# ------------------------------------------------------------------ answers the kernel couldn't give

def answering(magics, owner, function, generator):
    # magics with generator answering function of owner
    return {**magics, owner: {**magics[owner], function: generator}}

KIND = _socket.socket
PEER = ("203.0.113.7", 80)

def a_peer(request):
    return b"hello"

def refused(answer_owner, function, generator, program, **written):
    magics = answering(network(peers={PEER: a_peer}, **written), answer_owner, function, generator)
    return run_model(models, magics)(program)

def connected_to_peer(then):
    def program():
        with socket.create_connection(PEER) as sock:
            return then(sock)
    return program

@pytest.mark.parametrize("function, generator, program, broken", [
    (KIND.recv, lambda self, size, flags=0: [b"x" * (size + 1)], connected_to_peer(lambda s: s.recv(4)), r"len\(data\) <= bufsize"),
    (KIND.recv, lambda self, size, flags=0: [TimeoutError("timed out")], connected_to_peer(lambda s: s.recv(4)),
     r"drew \[TimeoutError\('timed out'\)\]"),
    (KIND.recv, lambda self, size, flags=0: [BlockingIOError(errno.EAGAIN, "x")],
     connected_to_peer(lambda s: (s.settimeout(5), s.recv(4))), r"drew \[BlockingIOError\(11"),
    (KIND.recv, lambda self, size, flags=0: [b"hello" if flags else b"world"],
     connected_to_peer(lambda s: (s.recv(5, S.MSG_PEEK), s.recv(5))), r"drew \[b'world'\]"),
    (KIND.send, lambda self, data, flags=0: [0], connected_to_peer(lambda s: s.send(b"abc")), "0 < sent"),
    (KIND.connect, lambda self, address: [BlockingIOError(errno.EINPROGRESS, "x")],
     connected_to_peer(lambda s: None), r"drew \[BlockingIOError\(115"),
    (KIND.connect, lambda self, address: [S.gaierror(S.EAI_NONAME, "x")], lambda: socket.socket().connect(PEER),
     r"drew \[gaierror"),
    (KIND.getsockname, lambda self: [("127.0.0.1", 9)],
     lambda: (lambda s: (s.bind(("10.0.0.1", 7)), s.listen(), s.getsockname()))(socket.socket()), None),
    (KIND.getsockname, lambda self: [("::1", 9, 0, 0)], connected_to_peer(lambda s: s.getsockname()), "is_address"),
])
def test_the_socket_models_refuse_what_the_kernel_couldnt_answer(function, generator, program, broken):
    if broken is None:
        # bound to a port of its own, the kernel isn't asked for it
        assert run_model(models, answering(network(), KIND, function, generator))(program)[2] == ("10.0.0.1", 7)
        return
    with pytest.raises(BrokenVariant, match=broken):
        refused(KIND, function, generator, program)

def test_a_descriptor_open_already_is_refused():
    with pytest.raises(BrokenVariant, match="fd not in OPEN"):
        run_model(models, answering(network(), KIND, KIND.__init__, lambda self, *args: [7, 7]))(
            lambda: (socket.socket(), socket.socket()))

def test_the_resolver_models_refuse_what_it_couldnt_answer():
    wrong_port = lambda host, port, *args: [[(S.AF_INET, S.SOCK_STREAM, 6, "", ("127.0.0.1", 81))]]
    with pytest.raises(BrokenVariant, match=r"getaddrinfo\('localhost', 80\) drew"):
        run_model(models, answering(network(), socket, socket.getaddrinfo, wrong_port))(
            lambda: socket.getaddrinfo("localhost", 80))
    with pytest.raises(BrokenVariant, match=r"drew \['::1'\]"):
        run_model(models, answering(network(), _socket, _socket.gethostbyname, lambda name: ["::1"]))(
            lambda: socket.gethostbyname("localhost"))


# ------------------------------------------------------------------ a network written down

def fortune_server(request):
    assert request.startswith(b"GET /fortune HTTP/1.1\r\nHost: cookies.example\r\n")
    return b"HTTP/1.0 200 OK\r\nContent-Type: text/plain\r\n\r\nYou will write a model today"

WRITTEN = dict(hosts={"cookies.example": ["203.0.113.7"]}, peers={("203.0.113.7", 80): fortune_server})

def test_http_client_runs_on_a_network_written_down():
    def fetch():
        connection = http.client.HTTPConnection("cookies.example", timeout=5)
        connection.request("GET", "/fortune")
        response = connection.getresponse()
        return response.status, response.read()
    assert run_model(models, network(**WRITTEN))(fetch) == (200, b"You will write a model today")

def test_a_written_down_network_refuses_and_fails_to_resolve():
    connect = lambda host: socket.create_connection((host, 80))
    assert modelled(lambda: connect("203.0.113.8"), network(**WRITTEN))[1] is ConnectionRefusedError
    assert modelled(lambda: connect("nowhere.example"), network(**WRITTEN))[1] is socket.gaierror

def test_socketpair_ends_send_to_each_other():
    def talk():
        first, second = socket.socketpair()
        first.sendall(b"ping")
        heard = second.recv(10)
        second.shutdown(S.SHUT_WR)
        ended = first.recv(10)
        second.close()
        return heard, ended, outcome(lambda: first.send(b"x"))[1]
    assert run_model(models, network())(talk) == (b"ping", b"", BrokenPipeError)

def test_a_server_accepts_the_clients_written_down():
    clients = [(("198.51.100.4", 50000), b"hello"), (("198.51.100.5", 50001), b"hi")]
    def serve():
        heard = []
        with socket.create_server(("", 8080)) as server:
            for _ in clients:
                connection, address = server.accept()
                with connection:
                    heard.append((address, connection.recv(10), connection.recv(10)))
            server.settimeout(0.5)
            return heard, outcome(server.accept)[1]
    assert run_model(models, network(clients=clients))(serve) == (
        [(("198.51.100.4", 50000), b"hello", b""), (("198.51.100.5", 50001), b"hi", b"")], TimeoutError)

def test_datagrams_go_to_peers_and_their_answers_come_back():
    def ask():
        with socket.socket(S.AF_INET, S.SOCK_DGRAM) as sock:
            sock.sendto(b"who?", ("203.0.113.53", 53))
            return sock.recvfrom(100), sock.getsockname()
    answer, local = run_model(models, network(peers={("203.0.113.53", 53): lambda query: b"it's " + query}))(ask)
    assert answer == (b"it's who?", ("203.0.113.53", 53)) and local == ("0.0.0.0", 40000)

def test_waiting_forever_stops_the_run():
    with pytest.raises(RunningUnstatedModel, match="waits for what never comes"):
        run_model(models, network())(lambda: socket.socketpair()[0].recv(1))


# ------------------------------------------------------------------ compiled

def test_socket_models_compile_without_socket():
    files = compile_models(models, Compilation.MODEL_CHECKER)
    imports = [line for source in files.values() for line in source.splitlines() if line.startswith(("import ", "from "))]
    assert not [line for line in imports if line.split()[1].split(".")[0] in ("socket", "_socket")]
    assert "class AddressFamily(IntEnum):\n    AF_UNSPEC = 0\n" in files["socket.py"]
    assert "\n\nclass gaierror(OSError):\n" in files["_socket.py"] and "BrokenVariant(" in files["_socket.py"]

COMPILED_TALK = """\
import builtins, socket
values = iter([(10**7, 10**7 + 1), 4, b"ping", b"too long"])
builtins.magic = lambda *kinds: next(values)
install("compiled")
first, second = socket.socketpair()
first.sendall(b"ping")
print(type(first).accept.__module__, second.recv(4))
try:
    second.recv(4)
except AssertionError as broken:
    print(type(broken.args[0]).__name__)
"""

def test_compiled_socket_models_run_with_a_supplied_magic(tmp_path):
    write_package(compile_models(models, Compilation.MODEL_CHECKER), tmp_path / "compiled")
    script = inspect.getsource(install) + COMPILED_TALK
    run = subprocess.run([sys.executable, "-c", script], cwd=tmp_path, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert run.stdout.split("\n")[:2] == ["compiled.socket b'ping'", "BrokenVariant"]
