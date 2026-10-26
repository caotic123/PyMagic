# the model of socket: a copy of gaierror, which stands in for it in a run, and the names the models check
# against. Sockets aren't modelled

import socket

from PyMagic import model


@model(socket.gaierror)
class gaierror(OSError):
    pass


timeout = TimeoutError  # socket.timeout is the builtin TimeoutError

# named only: the object callers pass to mean socket's default timeout
_GLOBAL_DEFAULT_TIMEOUT = socket._GLOBAL_DEFAULT_TIMEOUT


@model(socket)
class _model_socket:
    # the copies of its classes
    gaierror = gaierror
