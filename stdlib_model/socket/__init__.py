# the model of socket, the Python half over _socket's model: its socket class, which adds files and enumerations
# to _socket's, and getaddrinfo(), whose answer is the resolver's, magic. create_connection(), create_server(),
# socketpair(), getfqdn() and makefile()'s SocketIO are copies of socket's own, on Linux. dup(), fromfd(),
# send_fds() and recv_fds() aren't modelled

import io
import socket as socket_module
import _socket

from socket import (
    AF_INET, AF_INET6, AF_UNIX, AI_PASSIVE, IPPROTO_IPV6, IPV6_V6ONLY, SO_REUSEADDR, SO_REUSEPORT, SOCK_STREAM,
    SOL_SOCKET, AddressFamily, SocketKind, getaddrinfo, getdefaulttimeout, gethostbyaddr, gethostname,
    has_dualstack_ipv6, has_ipv6,
)

from PyMagic import constraint, magic, model, named

from .._socket import c_int, c_long, gaierror, is_address, numeric_host, socket as SocketType
from ..errno import EAGAIN

timeout = TimeoutError  # socket.timeout is the builtin TimeoutError
error = OSError         # and socket.error OSError

# named only: the object callers pass to mean socket's default timeout
_GLOBAL_DEFAULT_TIMEOUT = named(socket_module._GLOBAL_DEFAULT_TIMEOUT)

# the errors that mean a non-blocking socket would have waited
_blocking_errnos = {EAGAIN}


# ------------------------------------------------------------------ copied from socket

def _intenum_converter(value, enum_klass):
    """Convert a numeric family value to an IntEnum member.

    If it's not a known member, return the numeric value itself.
    """
    try:
        return enum_klass(value)
    except ValueError:
        return value


@model(socket_module._GiveupOnSendfile)
class _GiveupOnSendfile(Exception):
    __module__ = "socket"


@model(socket_module.socket)
class socket(SocketType):

    """A subclass of _socket.socket adding the makefile() method."""

    __module__ = "socket"
    __slots__ = ["__weakref__", "_io_refs", "_closed"]

    def __init__(self, family=-1, type=-1, proto=-1, fileno=None):
        # For user code address family and type values are IntEnum members, but
        # for the underlying _socket.socket they're just integers. The
        # constructor of _socket.socket converts the given argument to an
        # integer automatically.
        if fileno is None:
            if family == -1:
                family = AF_INET
            if type == -1:
                type = SOCK_STREAM
            if proto == -1:
                proto = 0
        SocketType.__init__(self, family, type, proto, fileno)
        self._io_refs = 0
        self._closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        if not self._closed:
            self.close()

    def __repr__(self):
        """Wrap __repr__() to reveal the real class name and socket
        address(es).
        """
        closed = getattr(self, '_closed', False)
        s = "<%s.%s%s fd=%i, family=%s, type=%s, proto=%i" \
            % (self.__class__.__module__,
               self.__class__.__qualname__,
               " [closed]" if closed else "",
               self.fileno(),
               self.family,
               self.type,
               self.proto)
        if not closed:
            try:
                laddr = self.getsockname()
                if laddr:
                    s += ", laddr=%s" % str(laddr)
            except error:
                pass
            try:
                raddr = self.getpeername()
                if raddr:
                    s += ", raddr=%s" % str(raddr)
            except error:
                pass
        s += '>'
        return s

    def __getstate__(self):
        raise TypeError(f"cannot pickle {self.__class__.__name__!r} object")

    def accept(self):
        """accept() -> (socket object, address info)

        Wait for an incoming connection.  Return a new socket
        representing the connection, and the address of the client.
        For IP sockets, the address info is a pair (hostaddr, port).
        """
        fd, addr = self._accept()
        sock = socket(self.family, self.type, self.proto, fileno=fd)
        # Issue #7995: if no default timeout is set and the listening
        # socket had a (non-zero) timeout, force the new socket in blocking
        # mode to override platform-specific socket flags inheritance.
        if getdefaulttimeout() is None and self.gettimeout():
            sock.setblocking(True)
        return sock, addr

    def makefile(self, mode="r", buffering=None, *,
                 encoding=None, errors=None, newline=None):
        """makefile(...) -> an I/O stream connected to the socket

        The arguments are as for io.open() after the filename, except the only
        supported mode values are 'r' (default), 'w' and 'b'.
        """
        # XXX refactor to share code?
        if not set(mode) <= {"r", "w", "b"}:
            raise ValueError("invalid mode %r (only r, w, b allowed)" % (mode,))
        writing = "w" in mode
        reading = "r" in mode or not writing
        assert reading or writing
        binary = "b" in mode
        rawmode = ""
        if reading:
            rawmode += "r"
        if writing:
            rawmode += "w"
        raw = SocketIO(self, rawmode)
        self._io_refs += 1
        if buffering is None:
            buffering = -1
        if buffering < 0:
            buffering = io.DEFAULT_BUFFER_SIZE
        if buffering == 0:
            if not binary:
                raise ValueError("unbuffered streams must be binary")
            return raw
        if reading and writing:
            buffer = io.BufferedRWPair(raw, raw, buffering)
        elif reading:
            buffer = io.BufferedReader(raw, buffering)
        else:
            assert writing
            buffer = io.BufferedWriter(raw, buffering)
        if binary:
            return buffer
        encoding = io.text_encoding(encoding)
        text = io.TextIOWrapper(buffer, encoding, errors, newline)
        text.mode = mode
        return text

    def _sendfile_use_sendfile(self, file, offset=0, count=None):
        # the descriptors are the model's, which os.sendfile() can't send from: sendfile() sends with send()
        raise _GiveupOnSendfile(
            "os.sendfile() not available on this platform")

    def _sendfile_use_send(self, file, offset=0, count=None):
        self._check_sendfile_params(file, offset, count)
        if self.gettimeout() == 0:
            raise ValueError("non-blocking sockets are not supported")
        if offset:
            file.seek(offset)
        blocksize = min(count, 8192) if count else 8192
        total_sent = 0
        # localize variable access to minimize overhead
        file_read = file.read
        sock_send = self.send
        try:
            while True:
                if count:
                    blocksize = min(count - total_sent, blocksize)
                    if blocksize <= 0:
                        break
                data = memoryview(file_read(blocksize))
                if not data:
                    break  # EOF
                while True:
                    try:
                        sent = sock_send(data)
                    except BlockingIOError:
                        continue
                    else:
                        total_sent += sent
                        if sent < len(data):
                            data = data[sent:]
                        else:
                            break
            return total_sent
        finally:
            if total_sent > 0 and hasattr(file, 'seek'):
                file.seek(offset + total_sent)

    def _check_sendfile_params(self, file, offset, count):
        if 'b' not in getattr(file, 'mode', 'b'):
            raise ValueError("file should be opened in binary mode")
        if not self.type & SOCK_STREAM:
            raise ValueError("only SOCK_STREAM type sockets are supported")
        if count is not None:
            if not isinstance(count, int):
                raise TypeError(
                    "count must be a positive integer (got {!r})".format(count))
            if count <= 0:
                raise ValueError(
                    "count must be a positive integer (got {!r})".format(count))

    def sendfile(self, file, offset=0, count=None):
        """sendfile(file[, offset[, count]]) -> sent

        Send a file until EOF is reached by using high-performance
        os.sendfile() and return the total number of bytes which
        were sent.
        *file* must be a regular file object opened in binary mode.
        If os.sendfile() is not available (e.g. Windows) or file is
        not a regular file socket.send() will be used instead.
        *offset* tells from where to start reading the file.
        If specified, *count* is the total number of bytes to transmit
        as opposed to sending the file until EOF is reached.
        File position is updated on return or also in case of error in
        which case file.tell() can be used to figure out the number of
        bytes which were sent.
        The socket must be of SOCK_STREAM type.
        Non-blocking sockets are not supported.
        """
        try:
            return self._sendfile_use_sendfile(file, offset, count)
        except _GiveupOnSendfile:
            return self._sendfile_use_send(file, offset, count)

    def _decref_socketios(self):
        if self._io_refs > 0:
            self._io_refs -= 1
        if self._closed:
            self.close()

    def _real_close(self, _ss=SocketType):
        # This function should not reference any globals. See issue #808164.
        _ss.close(self)

    def close(self):
        # This function should not reference any globals. See issue #808164.
        self._closed = True
        if self._io_refs <= 0:
            self._real_close()

    def detach(self):
        """detach() -> file descriptor

        Close the socket object without closing the underlying file descriptor.
        The object cannot be used after this call, but the file descriptor
        can be reused for other purposes.  The file descriptor is returned.
        """
        self._closed = True
        return super().detach()

    @property
    def family(self):
        """Read-only access to the address family for this socket.
        """
        return _intenum_converter(super().family, AddressFamily)

    @property
    def type(self):
        """Read-only access to the socket type.
        """
        return _intenum_converter(super().type, SocketKind)

    def get_inheritable(self):
        # the flag is the kernel's, of a descriptor a closed socket hasn't got
        return self._open().inheritable

    def set_inheritable(self, inheritable):
        inheritable = c_int(inheritable)
        self._open().inheritable = bool(inheritable)
    get_inheritable.__doc__ = "Get the inheritable flag of the socket"
    set_inheritable.__doc__ = "Set the inheritable flag of the socket"


@model(socket_module.SocketIO)
class SocketIO(io.RawIOBase):

    """Raw I/O implementation for stream sockets.

    This class supports the makefile() method on sockets.  It provides
    the raw I/O interface on top of a socket object.
    """

    # One might wonder why not let FileIO do the job instead.  There are two
    # main reasons why FileIO is not adapted:
    # - it wouldn't work under Windows (where you can't used read() and
    #   write() on a socket handle)
    # - it wouldn't work with socket timeouts (FileIO would ignore the
    #   timeout and consider the socket non-blocking)

    # XXX More docs

    __module__ = "socket"

    def __init__(self, sock, mode):
        if mode not in ("r", "w", "rw", "rb", "wb", "rwb"):
            raise ValueError("invalid mode: %r" % mode)
        io.RawIOBase.__init__(self)
        self._sock = sock
        if "b" not in mode:
            mode += "b"
        self._mode = mode
        self._reading = "r" in mode
        self._writing = "w" in mode
        self._timeout_occurred = False

    def readinto(self, b):
        """Read up to len(b) bytes into the writable buffer *b* and return
        the number of bytes read.  If the socket is non-blocking and no bytes
        are available, None is returned.

        If *b* is non-empty, a 0 return value indicates that the connection
        was shutdown at the other end.
        """
        self._checkClosed()
        self._checkReadable()
        if self._timeout_occurred:
            raise OSError("cannot read from timed out object")
        while True:
            try:
                return self._sock.recv_into(b)
            except timeout:
                self._timeout_occurred = True
                raise
            except error as e:
                if e.errno in _blocking_errnos:
                    return None
                raise

    def write(self, b):
        """Write the given bytes or bytearray object *b* to the socket
        and return the number of bytes written.  This can be less than
        len(b) if not all data could be written.  If the socket is
        non-blocking and no bytes could be written None is returned.
        """
        self._checkClosed()
        self._checkWritable()
        try:
            return self._sock.send(b)
        except error as e:
            # XXX what about EINTR?
            if e.errno in _blocking_errnos:
                return None
            raise

    def readable(self):
        """True if the SocketIO is open for reading.
        """
        if self.closed:
            raise ValueError("I/O operation on closed socket.")
        return self._reading

    def writable(self):
        """True if the SocketIO is open for writing.
        """
        if self.closed:
            raise ValueError("I/O operation on closed socket.")
        return self._writing

    def seekable(self):
        """True if the SocketIO is open for seeking.
        """
        if self.closed:
            raise ValueError("I/O operation on closed socket.")
        return super().seekable()

    def fileno(self):
        """Return the file descriptor of the underlying socket.
        """
        self._checkClosed()
        return self._sock.fileno()

    @property
    def name(self):
        if not self.closed:
            return self.fileno()
        else:
            return -1

    @property
    def mode(self):
        return self._mode

    def close(self):
        """Close the SocketIO object.  This doesn't close the underlying
        socket, except if all references to it have disappeared.
        """
        if self.closed:
            return
        io.RawIOBase.close(self)
        self._sock._decref_socketios()
        self._sock = None


# ------------------------------------------------------------------ what a resolver may answer

def lookup_host(host):
    # host as getaddrinfo() hands it to the resolver: None, bytes, or a str in IDNA
    if host is None or isinstance(host, bytes):
        return host
    if isinstance(host, str):
        return host.encode("idna")
    raise TypeError("getaddrinfo() argument 1 must be string or None")

def lookup_service(port):
    # port as getaddrinfo() hands it to the resolver: None, or bytes, an int's digits
    if type(port) is int:
        return str(c_long(port)).encode()
    if isinstance(port, str):
        return port.encode("utf-8")
    if port is None or isinstance(port, bytes):
        return port
    raise OSError("Int or String expected")

def is_resolved(entry, host, service, family, kind, proto, flags):
    # whether entry, (family, type, proto, canonname, address), is one getaddrinfo() may give for host and
    # service with the hints: of the family, type and protocol asked for, at the port of a numeric service, and at
    # the address a numeric host is, or that of this host or any when there's none
    if not (isinstance(entry, tuple) and len(entry) == 5 and isinstance(entry[3], str)):
        return False
    af, socktype, protocol, _, address = entry
    if not all(isinstance(number, int) for number in (af, socktype, protocol)):
        return False
    if family and af != family or kind and socktype != kind or proto and protocol != proto:
        return False
    if af not in (AF_INET, AF_INET6):
        return True
    if not is_address(af, address):
        return False
    if address[1] != (0 if service is None else int(service) if service.isdigit() else address[1]):
        return False
    if host is None:
        return address[0] == ({AF_INET: "0.0.0.0", AF_INET6: "::"} if flags & AI_PASSIVE else
                              {AF_INET: "127.0.0.1", AF_INET6: "::1"})[af]
    numeric = {} if host in (b"", b"<broadcast>") else {af: numeric_host(af, host) for af in (AF_INET, AF_INET6)}
    if any(numeric.values()):
        return address[0] == numeric[af]
    return True



@model(socket_module)
class _model_socket:
    # the copies of its classes
    socket = socket
    SocketIO = SocketIO
    _GiveupOnSendfile = _GiveupOnSendfile

    # ---------------------------------------------------------------- name lookups: the resolver's answers

    @model(socket_module.getaddrinfo)
    def getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        # the addresses to reach host's port at, as the resolver answers
        family, kind, proto, flags = c_int(family), c_int(type), c_int(proto), c_int(flags)
        name, service = lookup_host(host), lookup_service(port)
        answer = magic(list, OSError)
        constraint(isinstance(answer, list) and len(answer) > 0 and
                   all(is_resolved(entry, name, service, family, kind, proto, flags) for entry in answer) or
                   isinstance(answer, gaierror))
        if isinstance(answer, OSError):
            raise answer
        return [(_intenum_converter(af, AddressFamily), _intenum_converter(socktype, SocketKind), protocol, canonname,
                 address) for af, socktype, protocol, canonname, address in answer]

    @model(socket_module.getfqdn)
    def getfqdn(name=''):
        """Get fully qualified domain name from name.

        An empty argument is interpreted as meaning the local host.

        First the hostname returned by gethostbyaddr() is checked, then
        possibly existing aliases. In case no FQDN is available and `name`
        was given, it is returned unchanged. If `name` was empty, '0.0.0.0' or '::',
        hostname from gethostname() is returned.
        """
        name = name.strip()
        if not name or name in ('0.0.0.0', '::'):
            name = gethostname()
        try:
            hostname, aliases, ipaddrs = gethostbyaddr(name)
        except error:
            pass
        else:
            aliases.insert(0, hostname)
            for name in aliases:
                if '.' in name:
                    break
            else:
                name = hostname
        return name

    # ---------------------------------------------------------------- sockets made in a call

    @model(socket_module.socketpair)
    def socketpair(family=None, type=SOCK_STREAM, proto=0):
        """socketpair([family[, type[, proto]]]) -> (socket object, socket object)

        Create a pair of socket objects from the sockets returned by the platform
        socketpair() function.
        The arguments are the same as for socket() except the default family is
        AF_UNIX if defined on the platform; otherwise, the default is AF_INET.
        """
        if family is None:
            try:
                family = AF_UNIX
            except NameError:
                family = AF_INET
        a, b = _socket.socketpair(family, type, proto)
        a = socket(family, type, proto, a.detach())
        b = socket(family, type, proto, b.detach())
        return a, b

    @model(socket_module.create_connection)
    def create_connection(address, timeout=_GLOBAL_DEFAULT_TIMEOUT,
                          source_address=None):
        """Connect to *address* and return the socket object.

        Convenience function.  Connect to *address* (a 2-tuple ``(host,
        port)``) and return the socket object.  Passing the optional
        *timeout* parameter will set the timeout on the socket instance
        before attempting to connect.  If no *timeout* is supplied, the
        global default timeout setting returned by :func:`getdefaulttimeout`
        is used.  If *source_address* is set it must be a tuple of (host, port)
        for the socket to bind as a source address before making the connection.
        A host of '' or port 0 tells the OS to use the default.
        """

        host, port = address
        err = None
        for res in getaddrinfo(host, port, 0, SOCK_STREAM):
            af, socktype, proto, canonname, sa = res
            sock = None
            try:
                sock = socket(af, socktype, proto)
                if timeout is not _GLOBAL_DEFAULT_TIMEOUT:
                    sock.settimeout(timeout)
                if source_address:
                    sock.bind(source_address)
                sock.connect(sa)
                # Break explicitly a reference cycle
                err = None
                return sock

            except error as _:
                err = _
                if sock is not None:
                    sock.close()

        if err is not None:
            try:
                raise err
            finally:
                # Break explicitly a reference cycle
                err = None
        else:
            raise error("getaddrinfo returns an empty list")

    @model(socket_module.has_dualstack_ipv6)
    def has_dualstack_ipv6():
        """Return True if the platform supports creating a SOCK_STREAM socket
        which can handle both AF_INET and AF_INET6 (IPv4 / IPv6) connections.
        """
        # Linux has IPPROTO_IPV6 and IPV6_V6ONLY
        if not has_ipv6:
            return False
        try:
            with socket(AF_INET6, SOCK_STREAM) as sock:
                sock.setsockopt(IPPROTO_IPV6, IPV6_V6ONLY, 0)
                return True
        except error:
            return False

    @model(socket_module.create_server)
    def create_server(address, *, family=AF_INET, backlog=None, reuse_port=False,
                      dualstack_ipv6=False):
        """Convenience function which creates a SOCK_STREAM type socket
        bound to *address* (a 2-tuple (host, port)) and return the socket
        object.

        *family* should be either AF_INET or AF_INET6.
        *backlog* is the queue size passed to socket.listen().
        *reuse_port* dictates whether to use the SO_REUSEPORT socket option.
        *dualstack_ipv6*: if true and the platform supports it, it will
        create an AF_INET6 socket able to accept both IPv4 or IPv6
        connections. When false it will explicitly disable this option on
        platforms that enable it by default (e.g. Linux).
        """
        # Linux has SO_REUSEADDR, SO_REUSEPORT, IPPROTO_IPV6 and IPV6_V6ONLY
        if dualstack_ipv6:
            if not has_dualstack_ipv6():
                raise ValueError("dualstack_ipv6 not supported on this platform")
            if family != AF_INET6:
                raise ValueError("dualstack_ipv6 requires AF_INET6 family")
        sock = socket(family, SOCK_STREAM)
        try:
            try:
                sock.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
            except error:
                # Fail later on bind(), for platforms which may not
                # support this option.
                pass
            if reuse_port:
                sock.setsockopt(SOL_SOCKET, SO_REUSEPORT, 1)
            if has_ipv6 and family == AF_INET6:
                if dualstack_ipv6:
                    sock.setsockopt(IPPROTO_IPV6, IPV6_V6ONLY, 0)
                else:
                    sock.setsockopt(IPPROTO_IPV6, IPV6_V6ONLY, 1)
            try:
                sock.bind(address)
            except error as err:
                msg = '%s (while attempting to bind on address %r)' % \
                    (err.strerror, address)
                raise error(err.errno, msg) from None
            if backlog is None:
                sock.listen()
            else:
                sock.listen(backlog)
            return sock
        except error:
            sock.close()
            raise
