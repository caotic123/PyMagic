# magics for the standard library models from what the standard library itself does, so unlike the models these
# use it: a clock, fixed or the real one, randomness from a seeded generator, and the Unicode database's answers

import time
import random
import unicodedata
import encodings.idna
from types import SimpleNamespace

# the originals, kept from the models
real = SimpleNamespace(
    ctime=time.ctime, time=time.time, nameprep=encodings.idna.nameprep, normalize=unicodedata.normalize,
)


def outcome_of(function, *args):
    # what a function returns, or the exception it raises, as a magic value
    try:
        return function(*args)
    except Exception as error:
        return error

def stdlib_magics(now=None, seed=0):
    # the clock reads now, in seconds since the epoch (the real clock when it's None), and randomness comes from
    # a generator seeded with seed
    chance = random.Random(seed)

    def clock():
        return int(real.time()) if now is None else now

    def ctime(seconds=None):
        yield real.ctime(clock() if seconds is None else seconds)

    def gmtime(seconds=None):
        yield clock()

    def randrange(start, stop=None, step=1):
        yield chance.randrange(start, stop, step)

    def nameprep(label):
        yield outcome_of(real.nameprep, label)

    def normalize(form, unistr):
        yield real.normalize(form, unistr)

    return {
        time: {time.ctime: ctime, time.gmtime: gmtime},
        random: {random.randrange: randrange},
        encodings.idna: {encodings.idna.nameprep: nameprep},
        unicodedata: {unicodedata.normalize: normalize},
    }


# ------------------------------------------------------------------ the network

import errno
import socket
import _socket
import resource
import itertools
from dataclasses import dataclass, field

from PyMagic import RunningUnstatedModel

from . import _socket as model_socket

# the real sockets and lookups, kept from the models
network_of = SimpleNamespace(
    socket=_socket.socket, socketpair=_socket.socketpair, close=socket.close, getaddrinfo=socket.getaddrinfo,
    gethostbyname=socket.gethostbyname, gethostbyname_ex=socket.gethostbyname_ex,
    gethostbyaddr=socket.gethostbyaddr, gethostname=socket.gethostname, getnameinfo=socket.getnameinfo,
    getservbyname=socket.getservbyname, getservbyport=socket.getservbyport, getprotobyname=socket.getprotobyname,
)

def answered(call):
    # a magic generator drawing what call returns or raises, an OSError, once the model draws it
    try:
        yield call()
    except OSError as error:
        yield error

def socket_magics(lookups, opened, calls, pair, close):
    # the magics of the socket models: lookups by socket function, opened(self, family, type, proto, fileno) for a
    # new socket, calls by name of _socket.socket's method, pair(family, type, proto) for socketpair()'s and
    # close(fd) for socket.close()'s; getaddrinfo() is socket's, the others _socket's
    kind = network_of.socket
    return {
        socket: {socket.getaddrinfo: lookups["getaddrinfo"]},
        _socket: {**{getattr(_socket, name): lookup for name, lookup in lookups.items() if name != "getaddrinfo"},
                  _socket.socketpair: pair, _socket.close: close},
        kind: {kind.__init__: opened, **{getattr(kind, name): call for name, call in calls.items()}},
    }

SOCKET_CALLS = ("_accept", "bind", "close", "connect", "getpeername", "getsockname", "getsockopt", "listen", "recv",
                "recvfrom", "send", "sendto", "setsockopt", "shutdown")

# the real sockets under the models' with real_network(), by descriptor: open while the models hold them open,
# whichever run made them, as the kernel keeps a descriptor open while the process does
UNDERNEATH = {}

def real_network():
    # magics that ask the kernel and the network what the socket models draw: each model socket has a real one
    # underneath, made when the model's is, and a call that draws makes the same call on it, with the model's
    # timeout; the model's descriptors are the real ones
    sockets = UNDERNEATH

    def underneath(model):
        held = sockets[model.fileno()]
        held.settimeout(model.gettimeout())
        return held

    def kept(*held):
        # the real sockets kept for the models' new ones; those whose model let go of its descriptor without
        # closing it, as the garbage collector does, are closed
        for fd in [fd for fd in sockets if fd not in model_socket.OPEN]:
            sockets.pop(fd).close()
        sockets.update((each.fileno(), each) for each in held)
        return held

    def opened(self, family=-1, type=-1, proto=-1, fileno=None):
        return answered(lambda: kept(network_of.socket(family, type, proto))[0].fileno())

    def accepted(self):
        def made():
            fd, address = underneath(self)._accept()
            kept(network_of.socket(fileno=fd))
            return fd, address
        return answered(made)

    def closed(self):
        fd = self.fileno()
        return answered(lambda: sockets.pop(fd).close())

    def paired(family=socket.AF_UNIX, type=socket.SOCK_STREAM, proto=0):
        return answered(lambda: tuple(end.fileno() for end in kept(*network_of.socketpair(family, type, proto))))

    def call(name):
        return lambda self, *args: answered(lambda: getattr(underneath(self), name)(*args))

    calls = {name: call(name) for name in SOCKET_CALLS}
    calls.update(_accept=accepted, close=closed)
    lookups = {name: (lambda function: lambda *args, **kwargs: answered(lambda: function(*args, **kwargs)))(function)
               for name, function in vars(network_of).items() if name.startswith("get")}
    return socket_magics(lookups, opened, calls, paired, lambda fd: answered(lambda: sockets.pop(fd).close()))


@dataclass
class Connection:
    # what the written-down network keeps of a socket's connection: what has come for it and is still to come,
    # from its peer or from the other end of a socketpair(), and what it has sent
    peer: object
    answer: object = None  # the peer's, a function of what it's been sent, until it's asked for
    inbox: bytearray = field(default_factory=bytearray)
    datagrams: list = field(default_factory=list)
    sent: bytearray = field(default_factory=bytearray)
    other: object = None   # the other end of a socketpair()
    finished: bool = False # the peer sends no more
    writing: bool = True   # until it shuts down writing
    closed: bool = False

def network(hosts=None, peers=None, clients=(), hostname="model"):
    # magics for the socket models from a network written down. hosts, {name: [address, ...]}, is what the
    # resolver knows; peers, {(address, port): peer}, take connections and datagrams: a peer is a function of what
    # it's been sent, giving what it sends back once it's asked for, after which it ends a connection. clients,
    # [((address, port), request), ...], connect to a listening socket in turn, send their request and end. The
    # ends of a socketpair() send to each other. Descriptors are above any the process may have open, and ports
    # the kernel picks count from 40000. What waits for what never comes times out, or would block, or, blocking,
    # stops the run, as no magic can stand for it
    hosts, peers, clients = dict(hosts or {}), dict(peers or {}), iter(clients)
    connections, ports = {}, itertools.count(40000)
    descriptors = (fd for fd in itertools.count(max(resource.getrlimit(resource.RLIMIT_NOFILE)[1], 1 << 20))
                   if fd not in model_socket.OPEN)

    def resolved(name, family=socket.AF_INET):
        # the addresses of a host: itself when it's numeric, else those hosts lists, of the family
        numeric = model_socket.numeric_host(family, name.encode("idna") if isinstance(name, str) else name)
        if numeric is not None:
            return [numeric]
        found = [address for address in hosts.get(name.decode() if isinstance(name, bytes) else name, [])
                 if (":" in address) == (family == socket.AF_INET6)]
        if not found:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return found

    def waiting(self, flags):
        # what a call waiting for what never comes gives: its timeout, or would-block
        timeout = self.gettimeout()
        if timeout == 0 or flags & socket.MSG_DONTWAIT:
            return BlockingIOError(errno.EAGAIN, "Resource temporarily unavailable")
        if timeout is not None:
            return TimeoutError("timed out")
        raise RunningUnstatedModel(f"{self!r} waits for what never comes in the written-down network")

    def once(value):
        yield value

    def opened(self, family=-1, type=-1, proto=-1, fileno=None):
        return answered(lambda: next(descriptors))

    def connect(self, address):
        def made():
            peer = (resolved(address[0], self.family)[0], address[1])
            if self.type == socket.SOCK_STREAM and peer not in peers:
                return ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused")
            connections[self.fileno()] = Connection(peer, peers.get(peer))
            return None
        return answered(made)

    def accept(self):
        def made():
            client = next(clients, None)
            if client is None:
                return waiting(self, 0)
            fd, (address, request) = next(descriptors), client
            connections[fd] = Connection(address, inbox=bytearray(request), finished=True)
            return fd, address
        return answered(made)

    def datagram_to(self, peer, data):
        # a datagram to peer, whose answer comes back as one
        answer = peers.get(peer)
        if answer is not None:
            connections.setdefault(self.fileno(), Connection(None)).datagrams.append((bytes(answer(data)), peer))
        return len(data)

    def send(self, data, flags=0):
        def made():
            connection = connections[self.fileno()]
            if self.type != socket.SOCK_STREAM:
                return datagram_to(self, connection.peer, bytes(data))
            if connection.other is not None:
                if connection.other.closed:
                    return BrokenPipeError(errno.EPIPE, "Broken pipe")
                connection.other.inbox += data
            else:
                connection.sent += data
            return len(data)
        return answered(made)

    def sendto(self, data, *arguments):
        def made():
            host, port = arguments[-1][:2]
            return datagram_to(self, (resolved(host, self.family)[0], port), bytes(data))
        return answered(made)

    def recv(self, bufsize, flags=0):
        def made():
            connection = connections[self.fileno()]
            if connection.answer is not None:
                connection.inbox += connection.answer(bytes(connection.sent))
                connection.answer, connection.finished = None, True
            if connection.inbox:
                data = bytes(connection.inbox[:bufsize])
                if not flags & socket.MSG_PEEK:
                    del connection.inbox[:bufsize]
                return data
            other = connection.other
            if connection.finished or other is not None and (other.closed or not other.writing):
                return b""
            return waiting(self, flags)
        return answered(made)

    def recvfrom(self, bufsize, flags=0):
        def made():
            connection = connections.get(self.fileno())
            if connection is None or not connection.datagrams:
                return waiting(self, flags)
            return connection.datagrams[0] if flags & socket.MSG_PEEK else connection.datagrams.pop(0)
        return answered(made)

    def getsockname(self):
        # an address on this host, the loopback's unless it's bound to another, at a port of its own
        def made():
            if self.family == socket.AF_UNIX:
                return b"\0" + f"{next(ports):05x}".encode()
            known = model_socket.OPEN[self.fileno()].local
            host = known[0] or ("127.0.0.1" if self.family == socket.AF_INET else "::1")
            port = known[1] or next(ports)
            return (host, port) if self.family == socket.AF_INET else (host, port, 0, known[3])
        return answered(made)

    def getpeername(self):
        def made():
            peer = connections[self.fileno()].peer
            return peer if self.family == socket.AF_INET else (*peer, 0, 0)
        return answered(made)

    def close(self):
        fd = self.fileno()
        def made():
            connection = connections.pop(fd, None)
            if connection is not None:
                connection.closed = True
            return None
        return answered(made)

    def shutdown(self, how):
        def made():
            connection = connections.get(self.fileno())
            if connection is not None and how in (socket.SHUT_WR, socket.SHUT_RDWR):
                connection.writing = False
            return None
        return answered(made)

    def pair(family=socket.AF_UNIX, type=socket.SOCK_STREAM, proto=0):
        def made():
            ends = next(descriptors), next(descriptors)
            first, second = Connection(""), Connection("")
            first.other, second.other = second, first
            connections.update(zip(ends, (first, second)))
            return ends
        return answered(made)

    def getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        def made():
            kinds = [kind for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM, socket.SOCK_RAW) if type in (0, kind)]
            protocols = {socket.SOCK_STREAM: socket.IPPROTO_TCP, socket.SOCK_DGRAM: socket.IPPROTO_UDP, socket.SOCK_RAW: 0}
            service = port.decode() if isinstance(port, bytes) else port
            number = 0 if service is None else int(service) if str(service).isdigit() else socket.getservbyname(service)
            entries = []
            for af in [af for af in (socket.AF_INET, socket.AF_INET6) if family in (0, af)]:
                try:
                    if host is None:
                        addresses = ["0.0.0.0" if flags & socket.AI_PASSIVE else "127.0.0.1"] if af == socket.AF_INET \
                            else ["::" if flags & socket.AI_PASSIVE else "::1"]
                    else:
                        addresses = resolved(host, af)
                except OSError:
                    continue
                for address in addresses:
                    sockaddr = (address, number) if af == socket.AF_INET else (address, number, 0, 0)
                    entries += [(af, kind, protocols[kind], "", sockaddr) for kind in kinds]
            return entries or socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return answered(made)

    def gethostbyname(hostname):
        return answered(lambda: resolved(hostname)[0])

    def gethostbyname_ex(hostname):
        return answered(lambda: (hostname, [], resolved(hostname)))

    def gethostbyaddr(ip_address):
        def made():
            names = [name for name, addresses in hosts.items() if ip_address in addresses]
            if not names:
                return socket.herror(1, "Unknown host")
            return names[0], names[1:], [ip_address]
        return answered(made)

    def getnameinfo(sockaddr, flags):
        def made():
            names = [name for name, addresses in hosts.items() if sockaddr[0] in addresses]
            numeric = flags & socket.NI_NUMERICHOST or not names
            return sockaddr[0] if numeric else names[0], str(sockaddr[1])
        return answered(made)

    lookups = {
        "getaddrinfo": getaddrinfo, "gethostbyname": gethostbyname, "gethostbyname_ex": gethostbyname_ex,
        "gethostbyaddr": gethostbyaddr, "gethostname": lambda: once(hostname), "getnameinfo": getnameinfo,
        "getservbyname": lambda name, protocol=None: answered(lambda: network_of.getservbyname(name, protocol)),
        "getservbyport": lambda port, protocol=None: answered(lambda: network_of.getservbyport(port, protocol)),
        "getprotobyname": lambda name: answered(lambda: network_of.getprotobyname(name)),
    }
    calls = {
        "_accept": accept, "bind": lambda self, address: once(None), "close": close, "connect": connect,
        "getpeername": getpeername, "getsockname": getsockname,
        "getsockopt": lambda self, level, option, buflen=0: once(bytes(buflen) if buflen else 0),
        "listen": lambda self, *backlog: once(None), "recv": recv, "recvfrom": recvfrom, "send": send,
        "sendto": sendto, "setsockopt": lambda self, *args: once(None), "shutdown": shutdown,
    }
    return socket_magics(lookups, opened, calls, pair, lambda fd: once(None))
