import random
import binascii

import pytest

from stdlib_model.testing import each, modelled, modelled_each, outcome

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


ENCODED = [b"", b"aGk=", b"aGk", b"a", b"aG=k", b"a===", b"====", b"aGk=\n", b"a G k =", b"aGk=extra", b"Zg==Zg==",
           b"Zm9vYmFy", b"Zg", b"Zg=", b"Zg=a", b"*", b"=Zg==", b"Zg===", b"Z===", b"Zm9vY===", b"\xff\x80aGk=", "aGk=",
           "h\xe9", bytearray(b"aGk="), memoryview(b"aGk="), memoryview(b"aGk=aGk=")[::2], 5, None, [1]]

@pytest.mark.parametrize("data", ENCODED, ids=repr)
def test_a2b_base64_decodes_as_binascii(data):
    call = lambda: binascii.a2b_base64(data)
    assert modelled(call) == outcome(call)

def test_a2b_base64_of_random_text():
    # mostly the alphabet, with pads and characters out of it among it, and every encoding of random bytes
    pick = random.Random(2)
    characters = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/" + b"=" * 12 + b" \n*-_\x00\xff"
    texts = [bytes(pick.choice(characters) for _ in range(pick.randrange(24))) for _ in range(2000)]
    texts += [binascii.b2a_base64(bytes(pick.randrange(256) for _ in range(size))) for size in range(100)]
    calls = [lambda text=text: binascii.a2b_base64(text) for text in texts]
    assert modelled_each(calls) == each(calls)

def test_what_binascii_raises_where_it_isnt_modelled_is_its_error():
    # the model raises binascii.Error as it is, so that it's the class binascii's other functions raise too
    def unhexed():
        try:
            return binascii.unhexlify(b"zz")
        except binascii.Error as error:
            return "caught", str(error)
    assert modelled(unhexed) == outcome(unhexed) == ("returned", ("caught", "Non-hexadecimal digit found"))
