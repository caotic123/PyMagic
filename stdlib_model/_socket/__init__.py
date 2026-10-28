# the model of _socket, the C half of socket: its socket objects, exceptions and lookups, and the helpers for
# byte order, addresses and timeouts, written in plain Python as glibc does them. A socket keeps what the kernel
# keeps for it, open or closed, bound, listening, connecting or connected, shut down for reading or writing, and
# raises the errors that state decides as Linux does. What the kernel and the network decide is magic: the
# descriptor a socket gets, whether a bind, a listen, a connection, a shutdown or a close is made, the connections
# a listening socket accepts, what a peer sends, how much of what is sent goes out, the addresses and ports the
# kernel picks, the options it keeps, and the errors of all these. What one end of a socketpair() sends, the other
# receives as magic too: the magics connect them. Addresses are checked as _socket checks them for AF_INET,
# AF_INET6 and AF_UNIX, and taken as they are for the other families. The lookups are magic, the resolver's
# answers. recvmsg(), recvmsg_into(), sendmsg(), sendmsg_afalg(), dup(), sethostname() and the interface lookups
# aren't modelled

import _socket
import math
import os
import sys
from operator import index
from types import NoneType

from _socket import (
    AF_INET, AF_INET6, AF_UNIX, EAI_NONAME, IPPROTO_TCP, IPPROTO_UDP, MSG_DONTWAIT, MSG_PEEK, SHUT_RD, SHUT_RDWR,
    SHUT_WR, SO_ACCEPTCONN, SO_DOMAIN, SO_PROTOCOL, SO_TYPE, SOCK_CLOEXEC, SOCK_DGRAM, SOCK_NONBLOCK, SOCK_SEQPACKET,
    SOCK_STREAM, SOL_SOCKET,
)

from PyMagic import constraint, magic, model

from ..errno import (
    EACCES, EADDRINUSE, EADDRNOTAVAIL, EAFNOSUPPORT, EAGAIN, EALREADY, EBADF, ECONNABORTED, ECONNREFUSED,
    ECONNRESET, EDESTADDRREQ, EHOSTUNREACH, EINPROGRESS, EINVAL, EIO, EISCONN, ELOOP, EMFILE, EMSGSIZE,
    ENAMETOOLONG, ENETDOWN, ENETUNREACH, ENFILE, ENOBUFS, ENOENT, ENOMEM, ENOPROTOOPT, ENOTCONN, ENOTDIR,
    EOPNOTSUPP, EPERM, EPIPE, EPROTO, EPROTONOSUPPORT, EPROTOTYPE, EROFS, ESOCKTNOSUPPORT, ETIMEDOUT,
)


# ------------------------------------------------------------------ copied from _socket

@model(_socket.gaierror)
class gaierror(OSError):
    __module__ = "socket"


@model(_socket.herror)
class herror(OSError):
    __module__ = "socket"


# ------------------------------------------------------------------ errors

# what Linux says of the errors the model raises itself
STRERROR = {
    EBADF: "Bad file descriptor", EINVAL: "Invalid argument", EPIPE: "Broken pipe",
    ENOTCONN: "Transport endpoint is not connected", EISCONN: "Transport endpoint is already connected",
    EOPNOTSUPP: "Operation not supported", EDESTADDRREQ: "Destination address required",
    EMSGSIZE: "Message too long",
}

def failure(number):
    # the OSError Linux gives for number, of the subclass Python makes it
    return OSError(number, STRERROR[number])

# the errors the kernel and the network may give each call, beside those of waiting
CREATE_ERRORS = {EACCES, EAFNOSUPPORT, EINVAL, EMFILE, ENFILE, ENOBUFS, ENOMEM, EPERM, EPROTONOSUPPORT, EPROTOTYPE,
                 ESOCKTNOSUPPORT}
BIND_ERRORS = {EACCES, EADDRINUSE, EADDRNOTAVAIL, ELOOP, ENAMETOOLONG, ENOENT, ENOMEM, ENOTDIR, EPERM, EROFS}
LISTEN_ERRORS = {EADDRINUSE}
ACCEPT_ERRORS = {ECONNABORTED, EMFILE, ENFILE, ENOBUFS, ENOMEM, EPERM, EPROTO}
CONNECT_ERRORS = {EACCES, EADDRINUSE, EADDRNOTAVAIL, EAFNOSUPPORT, ECONNREFUSED, ECONNRESET, EHOSTUNREACH, ENETDOWN,
                  ENETUNREACH, ENOENT, ENOTDIR, EPERM, EPROTOTYPE, ETIMEDOUT}
SEND_ERRORS = {EACCES, ECONNREFUSED, ECONNRESET, EHOSTUNREACH, ENETDOWN, ENETUNREACH, ENOBUFS, ENOMEM, EPERM, EPIPE,
               ETIMEDOUT}
RECEIVE_ERRORS = {ECONNREFUSED, ECONNRESET, EHOSTUNREACH, ENETUNREACH, ENOMEM, ETIMEDOUT}
SHUTDOWN_ERRORS = {ENOTCONN}
PAIR_ERRORS = {EAFNOSUPPORT, EINVAL, EMFILE, ENFILE, EOPNOTSUPP, EPROTONOSUPPORT, EPROTOTYPE}
CLOSE_ERRORS = {EIO}
OPTION_ERRORS = {EACCES, EINVAL, ENOBUFS, ENOMEM, ENOPROTOOPT, EOPNOTSUPP, EPERM}

def waited(error, timeout, flags, errors):
    # whether a call that waits for the network could raise error: one of errors, Python's own "timed out" when
    # the socket has a timeout, or EAGAIN when it doesn't wait
    if not isinstance(error, OSError):
        return False
    if isinstance(error, TimeoutError) and error.errno is None:
        return timeout is not None and timeout > 0 and not flags & MSG_DONTWAIT
    if error.errno == EAGAIN:
        return timeout == 0 or bool(flags & MSG_DONTWAIT)
    return error.errno in errors


# ------------------------------------------------------------------ arguments as _socket parses them

def c_long(value):
    # value as _socket takes a C long
    number = index(value)
    if not -2**63 <= number < 2**63:
        raise OverflowError("Python int too large to convert to C long")
    return number

def c_int(value):
    # value as _socket takes a C int
    number = c_long(value)
    if number > 2**31 - 1:
        raise OverflowError("signed integer is greater than maximum")
    if number < -2**31:
        raise OverflowError("signed integer is less than minimum")
    return number

def c_size(value):
    # value as _socket takes a size
    number = index(value)
    if not -2**63 <= number < 2**63:
        raise OverflowError("Python int too large to convert to C ssize_t")
    return number

def unsigned(value):
    # value as _socket takes a C unsigned int, which keeps its low 32 bits
    return index(value) & 0xFFFFFFFF

def octets(data):
    # the bytes of a bytes-like object, as _socket sends them
    try:
        return memoryview(data).tobytes()
    except TypeError:
        raise TypeError(f"a bytes-like object is required, not '{type(data).__name__}'") from None

def writable(buffer, caller):
    # a buffer _socket receives into, as bytes
    try:
        view = memoryview(buffer)
    except TypeError:
        view = None
    if view is None or view.readonly:
        raise TypeError(f"{caller}() argument 1 must be read-write bytes-like object, not {type(buffer).__name__}")
    return view.cast("B")


# ------------------------------------------------------------------ timeouts, in nanoseconds as _socket keeps them

NS = 10**9

def timeout_ns(value):
    # a timeout in seconds as nanoseconds, rounded away from zero, or None for none; refused as _socket refuses it
    if value is None:
        return None
    if isinstance(value, float):
        if math.isnan(value):
            raise ValueError("Invalid value NaN (not a number)")
        scaled = value * NS
        if not -2.0**63 <= scaled < 2.0**63:
            raise OverflowError("timestamp too large to convert to C _PyTime_t")
        ns = math.ceil(scaled) if scaled >= 0 else math.floor(scaled)
    else:
        try:
            ns = c_long(value) * NS
        except OverflowError:
            ns = 2**63
        if not -2**63 <= ns < 2**63:
            raise OverflowError("timestamp too large to convert to C _PyTime_t")
    if ns < 0:
        raise ValueError("Timeout value out of range")
    return ns

def seconds(ns):
    # a timeout in nanoseconds as the seconds _socket gives back
    return None if ns is None else float(ns // NS) if ns % NS == 0 else ns / 1e9

# the timeout new sockets get, in nanoseconds
DEFAULTS = {"timeout": None}


# ------------------------------------------------------------------ IP addresses, as glibc reads and writes them

def ipv4_packed(text):
    # the 4 bytes of a dotted IPv4 address in decimal, as inet_pton() reads it, or None
    parts = text.split(".")
    if len(parts) != 4:
        return None
    for part in parts:
        if not part or not part.isascii() or not part.isdigit() or len(part) > 1 and part[0] == "0" or int(part) > 255:
            return None
    return bytes(int(part) for part in parts)

def ipv4_text(packed):
    return ".".join(str(octet) for octet in packed)

HEX_DIGITS = "0123456789abcdefABCDEF"

def ipv6_packed(text):
    # the 16 bytes of an IPv6 address, as glibc's inet_pton() reads it, or None
    if text == "" or text.startswith(":") and not text.startswith("::"):
        return None
    at = 1 if text.startswith("::") else 0
    words, gap, digits, value, token = [], None, 0, 0, at
    while at < len(text):
        character = text[at]
        at += 1
        if character in HEX_DIGITS:
            if digits == 4:
                return None
            value, digits = value << 4 | int(character, 16), digits + 1
            continue
        if character == ":":
            token = at
            if digits == 0:
                if gap is not None:
                    return None
                gap = len(words)
                continue
            if at == len(text) or len(words) == 8:
                return None
            words.append(value)
            digits = value = 0
            continue
        if character == "." and len(words) <= 6:
            quad = ipv4_packed(text[token:])
            if quad is None:
                return None
            words += [quad[0] << 8 | quad[1], quad[2] << 8 | quad[3]]
            digits = 0
            break
        return None
    if digits:
        if len(words) == 8:
            return None
        words.append(value)
    if gap is not None:
        if len(words) == 8:
            return None
        words[gap:gap] = [0] * (8 - len(words))
    if len(words) != 8:
        return None
    return b"".join(word.to_bytes(2, "big") for word in words)

def ipv6_text(packed):
    # the text of 16 bytes of an IPv6 address, as glibc's inet_ntop() writes it: the longest run of two or more
    # zero words as "::", and an IPv4 address the last 4 bytes hold in dots after 6 zero words, or ::ffff
    words = [packed[at] << 8 | packed[at + 1] for at in range(0, 16, 2)]
    best, run = (-1, 0), (-1, 0)
    for at, word in enumerate(words + [1]):
        if word == 0:
            run = (at, 1) if run[0] == -1 else (run[0], run[1] + 1)
        elif run[0] != -1:
            best = run if best[0] == -1 or run[1] > best[1] else best
            run = (-1, 0)
    if best[1] < 2:
        best = (-1, 0)
    text = ""
    for at, word in enumerate(words):
        if best[0] != -1 and best[0] <= at < best[0] + best[1]:
            text += ":" if at == best[0] else ""
            continue
        if at:
            text += ":"
        if at == 6 and best[0] == 0 and (best[1] == 6 or best[1] == 5 and words[5] == 0xFFFF):
            return text + ipv4_text(packed[12:])
        text += f"{word:x}"
    if best[0] != -1 and best[0] + best[1] == 8:
        text += ":"
    return text

def is_ipv4(text):
    # whether text is an IPv4 address as the kernel gives one
    return isinstance(text, str) and ipv4_packed(text) is not None

def is_ipv6(text):
    # whether text is an IPv6 address as the kernel gives one: as inet_ntop() writes it
    packed = ipv6_packed(text) if isinstance(text, str) else None
    return packed is not None and ipv6_text(packed) == text


# ------------------------------------------------------------------ addresses

def host_bytes(host):
    # a host as _socket hands it to the kernel: bytes, or a str in IDNA unless it's ASCII
    if isinstance(host, (bytes, bytearray)):
        name = bytes(host)
    elif isinstance(host, str):
        try:
            name = host.encode("ascii") if host.isascii() else host.encode("idna")
        except UnicodeError:
            raise TypeError("encoding of hostname failed") from None
    else:
        raise TypeError(f"str, bytes or bytearray expected, not {type(host).__name__}")
    if b"\0" in name:
        raise TypeError("host name must not contain null character")
    return name

def numeric_host(family, name):
    # the text of a host the kernel takes as it is: "" for any address, "<broadcast>", or a numeric address of
    # family; None for a name, which resolving makes an address
    if name == b"":
        return "0.0.0.0" if family == AF_INET else "::"
    if name == b"<broadcast>":
        if family != AF_INET:
            raise OSError("address family mismatched")
        return "255.255.255.255"
    text = name.decode("latin-1")
    if family == AF_INET:
        packed = ipv4_packed(text)
        return None if packed is None else ipv4_text(packed)
    packed = None if "%" in text else ipv6_packed(text)
    return None if packed is None else ipv6_text(packed)

def port_of(port, caller):
    try:
        return c_int(port)
    except OverflowError:
        raise OverflowError(f"{caller}(): port must be 0-65535.") from None

def address_of(family, address, caller):
    # address as the kernel takes it from caller, refused as _socket refuses it: (host, port) for AF_INET and
    # (host, port, flowinfo, scope_id) for AF_INET6, a numeric host in its canonical text and None for a name; the
    # path as getsockname() gives it back for AF_UNIX; that of any other family as it is
    if family == AF_INET:
        if not isinstance(address, tuple):
            raise TypeError(f"{caller}(): AF_INET address must be tuple, not {type(address).__name__}")
        if len(address) != 2:
            raise TypeError("AF_INET address must be a pair (host, port)")
        name, port = host_bytes(address[0]), port_of(address[1], caller)
        host = numeric_host(AF_INET, name)
        if not 0 <= port <= 0xFFFF:
            raise OverflowError(f"{caller}(): port must be 0-65535.")
        return host, port
    if family == AF_INET6:
        if not isinstance(address, tuple):
            raise TypeError(f"{caller}(): AF_INET6 address must be tuple, not {type(address).__name__}")
        if not 2 <= len(address) <= 4:
            raise TypeError("AF_INET6 address must be a tuple (host, port[, flowinfo[, scopeid]])")
        name, port = host_bytes(address[0]), port_of(address[1], caller)
        flowinfo, scope_id = [unsigned(part) for part in address[2:]] + [0] * (4 - len(address))
        host = numeric_host(AF_INET6, name)
        if not 0 <= port <= 0xFFFF:
            raise OverflowError(f"{caller}(): port must be 0-65535.")
        if flowinfo > 0xFFFFF:
            raise OverflowError(f"{caller}(): flowinfo must be 0-1048575.")
        return host, port, flowinfo, scope_id
    if family == AF_UNIX:
        path = octets(os.fsencode(address) if isinstance(address, str) else address)
        if len(path) > (108 if path[:1] in (b"", b"\0") else 107):
            raise OSError("AF_UNIX path too long")
        # an abstract one stays bytes; "" binds to one the kernel picks, which stays b"" until it's asked
        return path if path[:1] in (b"", b"\0") else os.fsdecode(path)
    return address

def connected_to(peer):
    # the address the kernel connects to: any address is this host's own
    if isinstance(peer, tuple) and peer[0] in ("0.0.0.0", "::"):
        peer = ("127.0.0.1" if peer[0] == "0.0.0.0" else "::1", *peer[1:])
    if isinstance(peer, tuple) and len(peer) == 4:
        peer = (peer[0], peer[1], 0, peer[3])
    return peer

def unbound(family):
    # the address getsockname() gives before a socket is bound; None when it isn't known
    return {AF_INET: ("0.0.0.0", 0), AF_INET6: ("::", 0, 0, 0), AF_UNIX: ""}.get(family)

def is_known(address):
    # whether nothing of an address is left for the kernel to pick
    return address is not None and address != b"" and not (isinstance(address, tuple) and None in address)

def is_address(family, address):
    # whether address is one the kernel gives for family
    if family == AF_INET:
        return isinstance(address, tuple) and len(address) == 2 and is_ipv4(address[0]) and is_port(address[1])
    if family == AF_INET6:
        return isinstance(address, tuple) and len(address) == 4 and is_ipv6(address[0]) and is_port(address[1]) \
            and type(address[2]) is int and 0 <= address[2] <= 0xFFFFF and type(address[3]) is int and \
            0 <= address[3] <= 0xFFFFFFFF
    if family == AF_UNIX:
        return isinstance(address, (str, bytes))
    return True

def is_port(port):
    return type(port) is int and 0 <= port <= 0xFFFF

def fits(address, known):
    # whether an address the kernel gives has what's known of it already
    if isinstance(known, tuple):
        return isinstance(address, tuple) and len(address) == len(known) and \
            all(part is None or part == given for part, given in zip(known, address))
    if known == b"":
        return isinstance(address, bytes) and address[:1] == b"\0"
    return known is None or address == known


# ------------------------------------------------------------------ what the kernel keeps

class Description:
    # what the kernel keeps for an open socket, shared by the socket objects of its descriptor
    def __init__(self, family, kind, proto):
        self.family, self.kind, self.proto = family, kind, proto
        self.local = None        # the address bound to, with None for what the kernel picks until it's asked
        self.peer = None         # the address connected to, likewise
        self.listening = False
        self.connecting = False  # a connect() that hasn't finished
        self.reading = self.writing = True
        self.ended = False       # what the peer sends has ended
        self.peeked = b""        # what recv() showed with MSG_PEEK, which the next recv() starts with
        self.options = {}        # {(level, option): value} as set
        self.inheritable = False

    @property
    def connected_kind(self):
        # whether it's a kind of socket that connects before it sends: a stream, or a sequence of packets
        return self.kind in (SOCK_STREAM, SOCK_SEQPACKET)

    @property
    def protocol(self):
        # the protocol the kernel says the socket has, which 0 lets it pick
        if self.proto or self.family not in (AF_INET, AF_INET6):
            return self.proto
        return {SOCK_STREAM: IPPROTO_TCP, SOCK_DGRAM: IPPROTO_UDP}.get(self.kind, 0)

    def bound(self, wildcard):
        # binds the socket to a port the kernel picks, as listen(), connect() and sendto() do unbound: on any of
        # this host's addresses, or on the one the kernel routes from
        if self.local is None:
            host = {AF_INET: "0.0.0.0", AF_INET6: "::"}.get(self.family) if wildcard else None
            self.local = {AF_INET: (host, None), AF_INET6: (host, None, 0, 0)}.get(self.family)

# the open sockets, by descriptor
OPEN = {}

# the options whose value the kernel gives back as 1 for any other than 0: SO_REUSEADDR, SO_BROADCAST,
# SO_KEEPALIVE, SO_REUSEPORT, TCP_NODELAY and IPV6_V6ONLY, as (level, option)
FLAGS = {(SOL_SOCKET, 2), (SOL_SOCKET, 6), (SOL_SOCKET, 9), (SOL_SOCKET, 15), (IPPROTO_TCP, 1), (41, 26)}

# the largest datagram the kernel sends, by family
DATAGRAM_LIMITS = {AF_INET: 65507, AF_INET6: 65527}


@model(_socket.socket)
class socket:
    """socket(family=AF_INET, type=SOCK_STREAM, proto=0) -> socket object
socket(family=-1, type=-1, proto=-1, fileno=None) -> socket object

Open a socket of the given type.  The family argument specifies the
address family; it defaults to AF_INET.  The type argument specifies
whether this is a stream (SOCK_STREAM, this is the default)
or datagram (SOCK_DGRAM) socket.  The protocol argument defaults to 0,
specifying the default protocol.  Keyword arguments are accepted.
The socket is created as non-inheritable.

When a fileno is passed in, family, type and proto are auto-detected,
unless they are explicitly set.

A socket object represents one endpoint of a network connection.

Methods of socket objects (keyword arguments not allowed):

_accept() -- accept connection, returning new socket fd and client address
bind(addr) -- bind the socket to a local address
close() -- close the socket
connect(addr) -- connect the socket to a remote address
connect_ex(addr) -- connect, return an error code instead of an exception
dup() -- return a new socket fd duplicated from fileno()
fileno() -- return underlying file descriptor
getpeername() -- return remote address [*]
getsockname() -- return local address
getsockopt(level, optname[, buflen]) -- get socket options
gettimeout() -- return timeout or None
listen([n]) -- start listening for incoming connections
recv(buflen[, flags]) -- receive data
recv_into(buffer[, nbytes[, flags]]) -- receive data (into a buffer)
recvfrom(buflen[, flags]) -- receive data and sender's address
recvfrom_into(buffer[, nbytes, [, flags])
  -- receive data and sender's address (into a buffer)
sendall(data[, flags]) -- send all data
send(data[, flags]) -- send data, may not send all of it
sendto(data[, flags], addr) -- send data to a given address
setblocking(bool) -- set or clear the blocking I/O flag
getblocking() -- return True if socket is blocking, False if non-blocking
setsockopt(level, optname, value[, optlen]) -- set socket options
settimeout(None | float) -- set or clear the timeout
shutdown(how) -- shut down traffic in one or both directions

 [*] not available on all platforms!"""

    __module__ = "_socket"
    __slots__ = ("_fd", "_family", "_type", "_proto", "_timeout")
    __getattribute__ = object.__getattribute__

    def __new__(cls, *args, **kwargs):
        # a socket with no descriptor, as _socket makes one before __init__() opens it
        made = object.__new__(cls)
        made._fd, made._family, made._type, made._proto, made._timeout = -1, 0, 0, 0, None
        return made

    @model(_socket.socket.__init__)
    def __init__(self, family=-1, type=-1, proto=-1, fileno=None):
        # a new socket, whose descriptor the kernel gives, or one open already
        family, type, proto = c_int(family), c_int(type), c_int(proto)
        if fileno is None:
            family, type, proto = (AF_INET if family == -1 else family), (SOCK_STREAM if type == -1 else type), \
                (0 if proto == -1 else proto)
            fd = magic(int, OSError)
            constraint(isinstance(fd, int) and fd >= 0 and fd not in OPEN or
                       isinstance(fd, OSError) and fd.errno in CREATE_ERRORS)
            if isinstance(fd, OSError):
                raise fd
            OPEN[fd] = Description(family, type & ~(SOCK_NONBLOCK | SOCK_CLOEXEC), proto)
        else:
            fd = c_long(fileno)
            if fd == -1:
                raise ValueError("negative file descriptor")
            if -1 in (family, type, proto):
                # what isn't given, the kernel tells of the descriptor
                if fd not in OPEN:
                    raise failure(EBADF)
                family = OPEN[fd].family if family == -1 else family
                type = OPEN[fd].kind if type == -1 else type
                proto = OPEN[fd].protocol if proto == -1 else proto
        self._fd, self._family, self._proto = fd, family, proto
        self._type = type & ~(SOCK_NONBLOCK | SOCK_CLOEXEC)
        self._timeout = 0 if type & SOCK_NONBLOCK else DEFAULTS["timeout"]

    def __del__(self):
        # a socket left open closes its descriptor, without asking the kernel how that went
        if self._fd != -1:
            OPEN.pop(self._fd, None)
            self._fd = -1

    def __repr__(self):
        return f"<socket object, fd={self._fd}, family={self._family}, type={self._type}, proto={self._proto}>"

    def _open(self):
        # what the kernel keeps for the socket, which a closed one has nothing of
        if self._fd not in OPEN:
            raise failure(EBADF)
        return OPEN[self._fd]

    family = property(lambda self: self._family, doc="the socket family")
    type = property(lambda self: self._type, doc="the socket type")
    proto = property(lambda self: self._proto, doc="the socket protocol")
    timeout = property(lambda self: seconds(self._timeout), doc="the socket timeout")

    def fileno(self):
        return self._fd

    def detach(self):
        fd, self._fd = self._fd, -1
        return fd

    def gettimeout(self):
        return seconds(self._timeout)

    def settimeout(self, timeout, /):
        # the timeout is the socket's before the kernel is told, which fails on a closed one
        self._timeout = timeout_ns(timeout)
        self._open()

    def setblocking(self, flag, /):
        self._timeout = None if c_long(flag) else 0
        self._open()

    def getblocking(self):
        return self._timeout != 0

    @model(_socket.socket.bind)
    def bind(self, address, /):
        # binds the socket, if the kernel lets it
        local = address_of(self._family, address, "bind")
        description = self._open()
        if description.local is not None:
            raise failure(EINVAL)
        error = magic(NoneType, OSError)
        constraint(error is None or isinstance(error, OSError) and error.errno in BIND_ERRORS or
                   isinstance(error, gaierror) and isinstance(local, tuple) and local[0] is None)
        if error is not None:
            raise error
        # port 0 is one the kernel picks; getsockname() gives no flow label
        if isinstance(local, tuple):
            local = (local[0], local[1] or None) + ((0, local[3]) if len(local) == 4 else ())
        description.local = local

    @model(_socket.socket.listen)
    def listen(self, backlog=128, /):
        # starts listening, bound to an address the kernel picks unless it's bound already
        backlog = c_int(backlog)
        description = self._open()
        if not description.connected_kind:
            raise failure(EOPNOTSUPP)
        if description.peer is not None:
            raise failure(EINVAL)
        error = magic(NoneType, OSError)
        constraint(error is None or isinstance(error, OSError) and error.errno in LISTEN_ERRORS)
        if error is not None:
            raise error
        description.bound(wildcard=True)
        description.listening = True

    @model(_socket.socket._accept)
    def _accept(self):
        # a connection the kernel has accepted: its descriptor, and the address of the peer
        description = self._open()
        if not description.connected_kind:
            raise failure(EOPNOTSUPP)
        if not description.listening:
            raise failure(EINVAL)
        accepted = magic(tuple, OSError)
        constraint(isinstance(accepted, tuple) and len(accepted) == 2 and type(accepted[0]) is int and
                   accepted[0] >= 0 and accepted[0] not in OPEN and is_address(self._family, accepted[1]) or
                   waited(accepted, self._timeout, 0, ACCEPT_ERRORS))
        if isinstance(accepted, OSError):
            raise accepted
        fd, address = accepted
        connection = Description(description.family, description.kind, description.proto)
        connection.local = description.local
        if isinstance(description.local, tuple) and description.local[0] in ("0.0.0.0", "::"):
            connection.local = (None, *description.local[1:])
        connection.peer = address
        OPEN[fd] = connection
        return fd, address

    @model(_socket.socket.connect)
    def connect(self, address, /):
        # connects the socket, if the network lets it
        peer = address_of(self._family, address, "connect")
        description = self._open()
        if description.connected_kind and (description.listening or description.peer is not None and
                                           not description.connecting):
            raise failure(EISCONN)
        error = magic(NoneType, OSError)
        constraint(error is None or waited(error, self._timeout, 0, CONNECT_ERRORS) or
                   isinstance(error, gaierror) and isinstance(peer, tuple) and peer[0] is None or
                   description.connecting and isinstance(error, OSError) and error.errno in (EALREADY, EISCONN) or
                   description.connected_kind and self._timeout == 0 and isinstance(error, BlockingIOError) and
                   error.errno == EINPROGRESS)
        if isinstance(error, gaierror):
            raise error
        # on the way when it doesn't wait or waits too long, connected once it's done; the kernel binds it
        pending = isinstance(error, BlockingIOError) and error.errno == EINPROGRESS or \
            isinstance(error, TimeoutError) and error.errno is None
        if error is None or pending or error.errno == EISCONN:
            description.peer = connected_to(peer)
            description.connecting = pending
            description.bound(wildcard=False)
        elif error.errno != EALREADY and description.connected_kind:
            description.peer, description.connecting = None, False
        if error is not None:
            raise error

    def connect_ex(self, address, /):
        # connect(), giving the error number of what fails in the kernel rather than raising it
        address_of(self._family, address, "connect_ex")
        try:
            socket.connect(self, address)
        except gaierror:
            raise
        except OSError as error:
            return EAGAIN if error.errno is None else error.errno
        return 0

    @model(_socket.socket.send)
    def send(self, data, flags=0, /):
        # sends what of data the kernel takes
        data, flags = octets(data), c_int(flags)
        description = self._open()
        if description.connected_kind:
            if description.peer is None or not description.writing:
                raise failure(EPIPE)
            if not data and not description.connecting:
                return 0
        else:
            if not description.writing:
                raise failure(EPIPE)
            if description.peer is None:
                raise failure(EDESTADDRREQ)
            if len(data) > DATAGRAM_LIMITS.get(self._family, len(data)):
                raise failure(EMSGSIZE)
        sent = magic(int, OSError)
        constraint(type(sent) is int and (0 < sent <= len(data) if description.connected_kind else sent == len(data))
                   or waited(sent, self._timeout, flags, SEND_ERRORS))
        if isinstance(sent, OSError):
            raise sent
        return sent

    def sendall(self, data, flags=0, /):
        # send() until the kernel has taken all of data
        view = memoryview(octets(data))
        while True:
            view = view[socket.send(self, view, flags):]
            if not view:
                return None

    @model(_socket.socket.sendto)
    def sendto(self, data, *arguments):
        # sends data to an address, which a connected stream ignores
        if len(arguments) not in (1, 2):
            raise TypeError(f"sendto() takes 2 or 3 arguments ({len(arguments) + 1} given)")
        data, flags = octets(data), (c_int(arguments[0]) if len(arguments) == 2 else 0)
        peer = address_of(self._family, arguments[-1], "sendto")
        description = self._open()
        if description.connected_kind:
            return socket.send(self, data, flags)
        if not description.writing:
            raise failure(EPIPE)
        if len(data) > DATAGRAM_LIMITS.get(self._family, len(data)):
            raise failure(EMSGSIZE)
        sent = magic(int, OSError)
        constraint(type(sent) is int and sent == len(data) or waited(sent, self._timeout, flags, SEND_ERRORS) or
                   isinstance(sent, gaierror) and isinstance(peer, tuple) and peer[0] is None)
        description.bound(wildcard=True)
        if isinstance(sent, OSError):
            raise sent
        return sent

    @model(_socket.socket.recv)
    def recv(self, bufsize, flags=0, /):
        # what the peer has sent, up to bufsize bytes, or b"" once it has ended
        bufsize, flags = c_size(bufsize), c_int(flags)
        if bufsize < 0:
            raise ValueError("negative buffersize in recv")
        if bufsize == 0:
            return b""
        description = self._open()
        if not description.connected_kind:
            return socket.recvfrom(self, bufsize, flags)[0]
        if description.peer is None or description.listening:
            raise failure(ENOTCONN)
        if description.ended:
            return b""
        # what MSG_PEEK showed is there already, so it comes first and nothing waits for it; shut down for
        # reading, nothing waits either
        peeked = description.peeked
        waits = description.reading and not peeked
        data = magic(bytes, OSError)
        constraint(isinstance(data, bytes) and len(data) <= bufsize and data[:len(peeked)] == peeked[:len(data)] and
                   len(data) >= min(bufsize, len(peeked)) or
                   not peeked and isinstance(data, OSError) and data.errno in RECEIVE_ERRORS or
                   waits and waited(data, self._timeout, flags, ()))
        if isinstance(data, OSError):
            raise data
        if data == b"" and description.reading:
            description.ended = True
        description.peeked = max(data, peeked, key=len) if flags & MSG_PEEK else peeked[len(data):]
        return data

    def recv_into(self, buffer, nbytes=0, flags=0):
        # recv() into buffer, giving how many bytes it holds
        view = writable(buffer, "recv_into")
        nbytes, flags = c_size(nbytes), c_int(flags)
        if nbytes < 0:
            raise ValueError("negative buffersize in recv_into")
        if nbytes == 0:
            nbytes = len(view)
        elif len(view) < nbytes:
            raise ValueError("buffer too small for requested bytes")
        data = socket.recv(self, nbytes, flags)
        view[:len(data)] = data
        return len(data)

    @model(_socket.socket.recvfrom)
    def recvfrom(self, bufsize, flags=0, /):
        # a datagram, cut to bufsize bytes, and its sender; a stream's data and None
        bufsize, flags = c_size(bufsize), c_int(flags)
        if bufsize < 0:
            raise ValueError("negative buffersize in recvfrom")
        description = self._open()
        if description.connected_kind:
            return socket.recv(self, bufsize, flags), None
        datagram = magic(tuple, OSError)
        constraint(isinstance(datagram, tuple) and len(datagram) == 2 and isinstance(datagram[0], bytes) and
                   (is_address(self._family, datagram[1]) or self._family == AF_UNIX and datagram[1] is None) and
                   (description.peer is None or datagram[1] == description.peer) or
                   waited(datagram, self._timeout, flags, RECEIVE_ERRORS))
        if isinstance(datagram, OSError):
            raise datagram
        data, address = datagram
        return data[:bufsize], address

    def recvfrom_into(self, buffer, nbytes=0, flags=0):
        # recvfrom() into buffer, giving how many bytes it holds and the sender
        view = writable(buffer, "recvfrom_into")
        nbytes, flags = c_size(nbytes), c_int(flags)
        if nbytes < 0:
            raise ValueError("negative buffersize in recvfrom_into")
        if nbytes == 0:
            nbytes = len(view)
        elif len(view) < nbytes:
            raise ValueError("nbytes is greater than the length of the buffer")
        data, address = socket.recvfrom(self, nbytes, flags)
        view[:len(data)] = data
        return len(data), address

    @model(_socket.socket.shutdown)
    def shutdown(self, how, /):
        # stops reading, writing or both, if the kernel lets it
        how = c_int(how)
        description = self._open()
        if how not in (SHUT_RD, SHUT_WR, SHUT_RDWR):
            raise failure(EINVAL)
        if description.peer is None and not description.listening:
            raise failure(ENOTCONN)
        error = magic(NoneType, OSError)
        constraint(error is None or isinstance(error, OSError) and error.errno in SHUTDOWN_ERRORS)
        if error is not None:
            raise error
        if how in (SHUT_RD, SHUT_RDWR):
            description.reading = description.listening = False
        if how in (SHUT_WR, SHUT_RDWR):
            description.writing = False

    @model(_socket.socket.close)
    def close(self):
        # closes the descriptor, which the kernel may say went wrong; it's closed all the same
        if self._fd == -1:
            return None
        fd, self._fd = self._fd, -1
        if OPEN.pop(fd, None) is None:
            raise failure(EBADF)
        error = magic(NoneType, OSError)
        constraint(error is None or isinstance(error, OSError) and error.errno in CLOSE_ERRORS)
        if error is not None:
            raise error

    @model(_socket.socket.getsockname)
    def getsockname(self):
        # the address the socket is bound to, which the kernel tells when it picked it
        description = self._open()
        local = unbound(self._family) if description.local is None else description.local
        if is_known(local):
            return local
        address = magic()
        constraint(is_address(self._family, address) and fits(address, local))
        description.local = address
        return address

    @model(_socket.socket.getpeername)
    def getpeername(self):
        # the address the socket is connected to, which the kernel tells when a name was resolved to it
        description = self._open()
        if description.peer is None or description.connecting:
            raise failure(ENOTCONN)
        if is_known(description.peer):
            return description.peer
        address = magic()
        constraint(is_address(self._family, address) and fits(address, description.peer))
        description.peer = address
        return address

    @model(_socket.socket.getsockopt)
    def getsockopt(self, level, option, buflen=0, /):
        # an option: what the socket is, or what the kernel says of it
        level, option, buflen = c_int(level), c_int(option), c_int(buflen)
        if buflen and not 0 < buflen <= 1024:
            raise OSError("getsockopt buflen out of range")
        description = self._open()
        if not buflen and level == SOL_SOCKET and option in (SO_TYPE, SO_DOMAIN, SO_PROTOCOL, SO_ACCEPTCONN):
            return {SO_TYPE: description.kind, SO_DOMAIN: description.family, SO_PROTOCOL: description.protocol,
                    SO_ACCEPTCONN: int(description.listening)}[option]
        if not buflen and (level, option) in FLAGS and isinstance(description.options.get((level, option)), int):
            return int(bool(description.options[(level, option)]))
        value = magic(int, bytes, OSError)
        constraint(type(value) is int and not buflen or isinstance(value, bytes) and buflen and len(value) <= buflen or
                   isinstance(value, OSError) and value.errno in OPTION_ERRORS)
        if isinstance(value, OSError):
            raise value
        return value

    @model(_socket.socket.setsockopt)
    def setsockopt(self, level, option, value, *optlen):
        # sets an option, if the kernel takes it
        if len(optlen) > 1:
            raise TypeError(f"setsockopt() takes at most 4 arguments ({len(optlen) + 3} given)")
        level, option = c_int(level), c_int(option)
        if optlen:
            if value is not None:
                raise TypeError(f"setsockopt() argument 3 must be None, not {type(value).__name__}")
            value = None, unsigned(optlen[0])
        elif isinstance(value, int):
            value = c_int(value)
        else:
            value = octets(value)
        description = self._open()
        error = magic(NoneType, OSError)
        constraint(error is None or isinstance(error, OSError) and error.errno in OPTION_ERRORS)
        if error is not None:
            raise error
        description.options[(level, option)] = value


# ------------------------------------------------------------------ glibc's parsing, byte order and answers

def aton_packed(text):
    # the 4 bytes of an IPv4 address as glibc's inet_aton() reads it, or None: up to 4 numbers, each decimal,
    # octal after a 0 or hexadecimal after 0x, the last filling the bytes the others leave; what follows a space
    # is ignored
    parts, at = [], 0
    while True:
        if at >= len(text) or text[at] not in "0123456789":
            return None
        if text.startswith(("0x", "0X"), at) and at + 2 < len(text) and text[at + 2] in HEX_DIGITS:
            base, digits, at = 16, HEX_DIGITS, at + 2
        else:
            base, digits = (8, "01234567") if text[at] == "0" else (10, "0123456789")
        start = at
        while at < len(text) and text[at] in digits:
            at += 1
        value = int(text[start:at], base)
        if value > 0xFFFFFFFF:
            return None
        if at < len(text) and text[at] == ".":
            if len(parts) > 2 or value > 0xFF:
                return None
            parts.append(value)
            at += 1
            continue
        break
    if at < len(text) and text[at] not in " \t\n\v\f\r":
        return None
    if value > [0xFFFFFFFF, 0xFFFFFF, 0xFFFF, 0xFF][len(parts)]:
        return None
    high = sum(part << 8 * (3 - position) for position, part in enumerate(parts))
    return (high | value).to_bytes(4, "big")

def swapped(value, size):
    # value with its bytes in network order, from this host's
    return int.from_bytes(value.to_bytes(size, sys.byteorder), "big")

def short_of(value, caller):
    # a 16-bit number, as htons() and ntohs() take it
    value = c_int(value)
    if value < 0:
        raise OverflowError(f"{caller}: can't convert negative Python int to C 16-bit unsigned integer")
    if value > 0xFFFF:
        raise OverflowError(f"{caller}: Python int too large to convert to C 16-bit unsigned integer")
    return value

def long_of(value):
    # a 32-bit number, as htonl() and ntohl() take it
    if not isinstance(value, int):
        raise TypeError(f"expected int, {type(value).__name__} found")
    if value < 0:
        raise OverflowError("can't convert negative value to unsigned int")
    if value >= 2**64:
        raise OverflowError("Python int too large to convert to C unsigned long")
    if value >= 2**32:
        raise OverflowError("int larger than 32 bits")
    return value

def text_argument(value, caller, position=1):
    # a str argument as _socket takes it: without null characters
    if not isinstance(value, str):
        raise TypeError(f"{caller}() argument {position} must be str, not {type(value).__name__}")
    if "\0" in value:
        raise ValueError("embedded null character")
    return value

def idna_argument(value, caller):
    # a host name argument as _socket takes it: bytes, a str in IDNA, without null bytes
    if isinstance(value, str):
        name = value.encode("idna")
    elif isinstance(value, (bytes, bytearray)):
        name = bytes(value)
    else:
        raise TypeError(f"{caller}() argument 1 must be str, bytes or bytearray, not {type(value).__name__}")
    if b"\0" in name:
        raise TypeError(f"{caller}() argument 1 must be encoded string without null bytes, not {type(value).__name__}")
    return name


def is_host(answer, numeric):
    # whether answer, (name, aliases, addresses), is one a resolver gives; for a numeric host, that host itself
    if not (isinstance(answer, tuple) and len(answer) == 3 and isinstance(answer[0], str) and
            isinstance(answer[1], list) and isinstance(answer[2], list) and answer[2]):
        return False
    if not all(isinstance(alias, str) for alias in answer[1]):
        return False
    if not all(is_ipv4(address) or is_ipv6(address) for address in answer[2]):
        return False
    return numeric is None or answer == (numeric, [], [numeric])


@model(_socket)
class _model__socket:
    # the copies of its sockets and exceptions
    socket = socket
    gaierror = gaierror
    herror = herror

    # ---------------------------------------------------------------- name lookups: the resolver's answers

    @model(_socket.gethostbyname)
    def gethostbyname(hostname, /):
        # the IPv4 address of hostname: itself when it's one, else the resolver's
        known = numeric_host(AF_INET, idna_argument(hostname, "gethostbyname"))
        if known is not None:
            return known
        address = magic(str, OSError)
        constraint(is_ipv4(address) or isinstance(address, gaierror))
        if isinstance(address, OSError):
            raise address
        return address

    @model(_socket.gethostbyname_ex)
    def gethostbyname_ex(hostname, /):
        # hostname's name, aliases and IPv4 addresses, as the resolver answers
        known = numeric_host(AF_INET, idna_argument(hostname, "gethostbyname_ex"))
        host = magic(tuple, OSError)
        constraint(is_host(host, known) and all(is_ipv4(address) for address in host[2]) or
                   isinstance(host, (gaierror, herror)))
        if isinstance(host, OSError):
            raise host
        return host

    @model(_socket.gethostbyaddr)
    def gethostbyaddr(ip_address, /):
        # the name, aliases and addresses of the host at ip_address, as the resolver answers
        idna_argument(ip_address, "gethostbyaddr")
        host = magic(tuple, OSError)
        constraint(is_host(host, None) or isinstance(host, (gaierror, herror)))
        if isinstance(host, OSError):
            raise host
        return host

    @model(_socket.gethostname)
    def gethostname():
        # this host's name, which the kernel keeps
        name = magic(str, OSError)
        if isinstance(name, OSError):
            raise name
        return name

    @model(_socket.getnameinfo)
    def getnameinfo(sockaddr, flags, /):
        # the host and the service of a numeric address, as the resolver names them
        flags = c_int(flags)
        if not isinstance(sockaddr, tuple):
            raise TypeError("getnameinfo() argument 1 must be a tuple")
        if not 2 <= len(sockaddr) <= 4 or not isinstance(sockaddr[0], str):
            raise TypeError("getnameinfo(): illegal sockaddr argument")
        host, port = text_argument(sockaddr[0], "getnameinfo"), c_int(sockaddr[1])
        if len(sockaddr) > 2 and sockaddr[2] & 0xFFFFFFFF > 0xFFFFF:
            raise OverflowError("getnameinfo(): flowinfo must be 0-1048575.")
        if aton_packed(host) is None and ipv6_packed(host.split("%")[0]) is None:
            raise gaierror(EAI_NONAME, "Name or service not known")
        if aton_packed(host) is not None and len(sockaddr) != 2:
            raise OSError("IPv4 sockaddr must be 2 tuple")
        named_as = magic(tuple, OSError)
        constraint(isinstance(named_as, tuple) and len(named_as) == 2 and
                   all(isinstance(part, str) for part in named_as) or isinstance(named_as, gaierror))
        if isinstance(named_as, OSError):
            raise named_as
        return named_as

    @model(_socket.getservbyname)
    def getservbyname(servicename, protocolname=None, /):
        # the port of a service, as the system's services list it
        text_argument(servicename, "getservbyname")
        if protocolname is not None:
            text_argument(protocolname, "getservbyname", 2)
        port = magic(int, OSError)
        constraint(isinstance(port, int) and 0 <= port <= 0xFFFF or
                   isinstance(port, OSError) and port.args == ("service/proto not found",))
        if isinstance(port, OSError):
            raise port
        return port

    @model(_socket.getservbyport)
    def getservbyport(port, protocolname=None, /):
        # the service at a port, as the system's services list it
        port = c_int(port)
        if protocolname is not None:
            text_argument(protocolname, "getservbyport", 2)
        if not 0 <= port <= 0xFFFF:
            raise OverflowError("getservbyport: port must be 0-65535.")
        service = magic(str, OSError)
        constraint(isinstance(service, str) or
                   isinstance(service, OSError) and service.args == ("port/proto not found",))
        if isinstance(service, OSError):
            raise service
        return service

    @model(_socket.getprotobyname)
    def getprotobyname(protocolname, /):
        # the number of a protocol, as the system's protocols list it
        text_argument(protocolname, "getprotobyname")
        number = magic(int, OSError)
        constraint(isinstance(number, int) and number >= 0 or
                   isinstance(number, OSError) and number.args == ("protocol not found",))
        if isinstance(number, OSError):
            raise number
        return number

    # ---------------------------------------------------------------- sockets made in a call

    @model(_socket.socketpair)
    def socketpair(family=AF_UNIX, type=SOCK_STREAM, proto=0, /):
        # two sockets connected to each other, whose descriptors the kernel gives
        family, kind, proto = c_int(family), c_int(type), c_int(proto)
        descriptors = magic(tuple, OSError)
        constraint(isinstance(descriptors, tuple) and len(descriptors) == 2 and family == AF_UNIX and
                   all(isinstance(fd, int) and fd >= 0 and fd not in OPEN for fd in descriptors) and
                   descriptors[0] != descriptors[1] or
                   isinstance(descriptors, OSError) and descriptors.errno in PAIR_ERRORS)
        if isinstance(descriptors, OSError):
            raise descriptors
        for fd in descriptors:
            OPEN[fd] = Description(family, kind & ~(SOCK_NONBLOCK | SOCK_CLOEXEC), proto)
            OPEN[fd].peer = ""
        return tuple(socket(family, kind, proto, fd) for fd in descriptors)

    @model(_socket.close)
    def close(fd, /):
        # closes a descriptor, which the kernel may say went wrong; it's closed all the same
        fd = c_long(fd)
        if OPEN.pop(fd, None) is None:
            raise failure(EBADF)
        failed = magic(NoneType, OSError)
        constraint(failed is None or isinstance(failed, OSError) and failed.errno in CLOSE_ERRORS)
        if failed is not None:
            raise failed

    # ---------------------------------------------------------------- timeouts, the model's own

    @model(_socket.getdefaulttimeout)
    def getdefaulttimeout():
        return seconds(DEFAULTS["timeout"])

    @model(_socket.setdefaulttimeout)
    def setdefaulttimeout(timeout, /):
        DEFAULTS["timeout"] = timeout_ns(timeout)

    # ---------------------------------------------------------------- byte order and addresses, as glibc does them

    @model(_socket.htons)
    def htons(x, /):
        return swapped(short_of(x, "htons"), 2)

    @model(_socket.ntohs)
    def ntohs(x, /):
        return swapped(short_of(x, "ntohs"), 2)

    @model(_socket.htonl)
    def htonl(x, /):
        return swapped(long_of(x), 4)

    @model(_socket.ntohl)
    def ntohl(x, /):
        return swapped(long_of(x), 4)

    @model(_socket.inet_aton)
    def inet_aton(string, /):
        packed = aton_packed(text_argument(string, "inet_aton"))
        if packed is None:
            raise OSError("illegal IP address string passed to inet_aton")
        return packed

    @model(_socket.inet_ntoa)
    def inet_ntoa(packed_ip, /):
        packed = octets(packed_ip)
        if len(packed) != 4:
            raise OSError("packed IP wrong length for inet_ntoa")
        return ipv4_text(packed)

    @model(_socket.inet_pton)
    def inet_pton(address_family, ip_string, /):
        address_family = c_int(address_family)
        text = text_argument(ip_string, "inet_pton", 2)
        if address_family not in (AF_INET, AF_INET6):
            raise OSError(EAFNOSUPPORT, "Address family not supported by protocol")
        packed = ipv4_packed(text) if address_family == AF_INET else ipv6_packed(text)
        if packed is None:
            raise OSError("illegal IP address string passed to inet_pton")
        return packed

    @model(_socket.inet_ntop)
    def inet_ntop(address_family, packed_ip, /):
        address_family, packed = c_int(address_family), octets(packed_ip)
        if address_family not in (AF_INET, AF_INET6):
            raise ValueError(f"unknown address family {address_family}")
        if len(packed) != (4 if address_family == AF_INET else 16):
            raise ValueError("invalid length of packed IP address string")
        return ipv4_text(packed) if address_family == AF_INET else ipv6_text(packed)
