import random
import binascii

import pytest

from stdlib_model.testing import modelled, outcome

DATA = [b"", b"a", b"ab", b"abc", b"abcd", bytes(range(256)), bytearray(b"xyz"), memoryview(b"hello"), "text", 5,
        None]

@pytest.mark.parametrize("newline", [True, False])
@pytest.mark.parametrize("data", DATA, ids=repr)
def test_b2a_base64_encodes_as_binascii(data, newline):
    call = lambda: binascii.b2a_base64(data, newline=newline)
    assert modelled(call) == outcome(call)

def test_b2a_base64_of_random_bytes():
    pick = random.Random(1)
    for size in range(100):
        data = bytes(pick.randrange(256) for _ in range(size))
        assert modelled(lambda: binascii.b2a_base64(data)) == outcome(lambda: binascii.b2a_base64(data))
