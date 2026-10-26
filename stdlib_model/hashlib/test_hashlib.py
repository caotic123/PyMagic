import random
import _hashlib
import hashlib

import pytest

from stdlib_model.testing import modelled, outcome

ALGORITHMS = ["md5", "sha1"]

def digests(name, chunks):
    # the digest of the chunks fed one by one, and of a copy taken halfway
    made = getattr(hashlib, name)()
    copied = None
    for at, chunk in enumerate(chunks):
        made.update(chunk)
        if at == len(chunks) // 2:
            copied = made.copy()
    return made.hexdigest(), made.digest(), copied and copied.hexdigest(), made.name, made.digest_size, \
        made.block_size

@pytest.mark.parametrize("name", ALGORITHMS)
def test_hashes_as_openssl(name):
    pick = random.Random(2)
    for size in [0, 1, 55, 56, 63, 64, 65, 127, 128, 1000]:
        data = bytes(pick.randrange(256) for _ in range(size))
        chunks = [data[i:i + 7] for i in range(0, len(data), 7)]
        assert modelled(lambda: digests(name, chunks)) == outcome(lambda: digests(name, chunks))
        assert modelled(lambda: getattr(hashlib, name)(data).hexdigest()) == \
            outcome(lambda: getattr(hashlib, name)(data).hexdigest())

@pytest.mark.parametrize("name", ALGORITHMS)
@pytest.mark.parametrize("data", ["text", 5, None, bytearray(b"ab"), memoryview(b"ab")], ids=repr)
def test_hashes_refuse_as_openssl(name, data):
    for call in (lambda: getattr(hashlib, name)(data).hexdigest(), lambda: getattr(hashlib, name)().update(data)):
        assert modelled(call) == outcome(call)

def test_hash_objects_are_the_copy_in_a_run():
    assert modelled(lambda: _hashlib.HASH()) == outcome(lambda: _hashlib.HASH())
    assert modelled(lambda: isinstance(hashlib.md5(), _hashlib.HASH)) == ("returned", True)
    assert modelled(lambda: repr(hashlib.sha1()).split(" @ ")[0]) == outcome(lambda: repr(hashlib.sha1()).split(" @ ")[0])
    assert modelled(lambda: hashlib.md5(b"a", usedforsecurity=False).hexdigest())[1] == hashlib.md5(b"a").hexdigest()
