import socket

from stdlib_model import socket as model_socket
from stdlib_model.testing import modelled, outcome

def test_gaierror_is_the_copy_in_a_run_and_leaves_it_as_the_original():
    assert modelled(lambda: socket.gaierror is model_socket.gaierror) == ("returned", True)
    def raises():
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
    assert modelled(raises) == outcome(raises)

def test_names_are_sockets():
    assert model_socket.timeout is socket.timeout
    assert model_socket._GLOBAL_DEFAULT_TIMEOUT is socket._GLOBAL_DEFAULT_TIMEOUT
