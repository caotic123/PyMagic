# models of the standard library modules the other models reach, a folder for each, named as the module is and
# laid out as the standard library is, even when only part of the module is modelled. Effects are magic: the
# clock, randomness, the Unicode database and the network; what Python does in C is written in plain Python;
# classes are copies that stand in for their originals in a run, sockets keeping the state the kernel keeps for
# them. errno and email.message hold only numbers and names

from ._socket import _model__socket
from .binascii import _model_binascii
from .encodings.idna import _model_idna
from .hashlib import _model_hashlib
from .http.client import _model_client
from .random import _model_random
from .re import _model_re
from .socket import _model_socket
from .time import _model_time
from .unicodedata import _model_unicodedata
from .zlib import _model_zlib

models = [
    _model__socket, _model_binascii, _model_idna, _model_hashlib, _model_client, _model_random, _model_re,
    _model_socket, _model_time, _model_unicodedata, _model_zlib,
]
