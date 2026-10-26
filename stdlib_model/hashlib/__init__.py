# the model of hashlib: md5() and sha1(), which hashlib has OpenSSL compute, written in plain Python (RFC 1321
# and RFC 3174), in a copy of the hash objects OpenSSL makes, which stands in for them in a run. The other
# algorithms aren't modelled

import _hashlib
import hashlib

from MagicF import model

MASK = 0xFFFFFFFF


def rotated(word, bits):
    return (word << bits | word >> (32 - bits)) & MASK


# md5: four rounds of 16 steps over the block's words in little endian
MD5_SHIFTS = [7, 12, 17, 22] * 4 + [5, 9, 14, 20] * 4 + [4, 11, 16, 23] * 4 + [6, 10, 15, 21] * 4
MD5_SINES = [
    0xd76aa478, 0xe8c7b756, 0x242070db, 0xc1bdceee, 0xf57c0faf, 0x4787c62a, 0xa8304613, 0xfd469501,
    0x698098d8, 0x8b44f7af, 0xffff5bb1, 0x895cd7be, 0x6b901122, 0xfd987193, 0xa679438e, 0x49b40821,
    0xf61e2562, 0xc040b340, 0x265e5a51, 0xe9b6c7aa, 0xd62f105d, 0x02441453, 0xd8a1e681, 0xe7d3fbc8,
    0x21e1cde6, 0xc33707d6, 0xf4d50d87, 0x455a14ed, 0xa9e3e905, 0xfcefa3f8, 0x676f02d9, 0x8d2a4c8a,
    0xfffa3942, 0x8771f681, 0x6d9d6122, 0xfde5380c, 0xa4beea44, 0x4bdecfa9, 0xf6bb4b60, 0xbebfbc70,
    0x289b7ec6, 0xeaa127fa, 0xd4ef3085, 0x04881d05, 0xd9d4d039, 0xe6db99e5, 0x1fa27cf8, 0xc4ac5665,
    0xf4292244, 0x432aff97, 0xab9423a7, 0xfc93a039, 0x655b59c3, 0x8f0ccc92, 0xffeff47d, 0x85845dd1,
    0x6fa87e4f, 0xfe2ce6e0, 0xa3014314, 0x4e0811a1, 0xf7537e82, 0xbd3af235, 0x2ad7d2bb, 0xeb86d391,
]

def md5_block(state, block):
    words = [int.from_bytes(block[i:i + 4], "little") for i in range(0, 64, 4)]
    a, b, c, d = state
    for step in range(64):
        if step < 16:
            mixed, word = (b & c) | (~b & d), step
        elif step < 32:
            mixed, word = (d & b) | (~d & c), (5 * step + 1) % 16
        elif step < 48:
            mixed, word = b ^ c ^ d, (3 * step + 5) % 16
        else:
            mixed, word = c ^ (b | (~d & MASK)), 7 * step % 16
        mixed = (mixed + a + MD5_SINES[step] + words[word]) & MASK
        a, d, c, b = d, c, b, (b + rotated(mixed, MD5_SHIFTS[step])) & MASK
    return [(x + y) & MASK for x, y in zip(state, (a, b, c, d))]

# sha1: 80 steps over the block's words in big endian, extended to 80
def sha1_block(state, block):
    words = [int.from_bytes(block[i:i + 4], "big") for i in range(0, 64, 4)]
    for i in range(16, 80):
        words.append(rotated(words[i - 3] ^ words[i - 8] ^ words[i - 14] ^ words[i - 16], 1))
    a, b, c, d, e = state
    for step in range(80):
        if step < 20:
            mixed, constant = (b & c) | (~b & d), 0x5A827999
        elif step < 40:
            mixed, constant = b ^ c ^ d, 0x6ED9EBA1
        elif step < 60:
            mixed, constant = (b & c) | (b & d) | (c & d), 0x8F1BBCDC
        else:
            mixed, constant = b ^ c ^ d, 0xCA62C1D6
        a, b, c, d, e = (rotated(a, 5) + mixed + e + constant + words[step]) & MASK, a, rotated(b, 30), c, d
    return [(x + y) & MASK for x, y in zip(state, (a, b, c, d, e))]

# name: (the starting state, the function of a block, the byte order, digest_size)
ALGORITHMS = {
    "md5": ([0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476], md5_block, "little", 16),
    "sha1": ([0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476, 0xC3D2E1F0], sha1_block, "big", 20),
}

def octets_of(data):
    # the bytes of what is hashed, refused as OpenSSL's hashes refuse it
    if isinstance(data, str):
        raise TypeError("Strings must be encoded before hashing")
    try:
        return memoryview(data).tobytes()
    except TypeError:
        raise TypeError("object supporting the buffer API required") from None


@model(_hashlib.HASH)
class HASH:
    """A hash is an object used to calculate a checksum of a string of information.

Methods:

update() -- updates the current digest with an additional string
digest() -- return the current digest value
hexdigest() -- return the current digest as a string of hexadecimal digits
copy() -- return a copy of the current hash object

Attributes:

name -- the hash algorithm being used by this object
digest_size -- number of bytes in this hashes output"""

    __slots__ = ("_name", "_state", "_pending", "_length")

    def __new__(cls, *args, **kwargs):
        raise TypeError("cannot create '_hashlib.HASH' instances")

    @property
    def name(self):
        return self._name

    @property
    def digest_size(self):
        return ALGORITHMS[self._name][3]

    @property
    def block_size(self):
        return 64

    def update(self, obj, /):
        """Update this hash object's state with the provided string."""
        octets = octets_of(obj)
        self._pending += octets
        self._length += len(octets)
        _, block_function, _, _ = ALGORITHMS[self._name]
        whole = len(self._pending) - len(self._pending) % 64
        for start in range(0, whole, 64):
            self._state = block_function(self._state, self._pending[start:start + 64])
        self._pending = self._pending[whole:]

    def digest(self):
        """Return the digest value as a bytes object."""
        _, block_function, order, _ = ALGORITHMS[self._name]
        # a 1 bit, zeros up to 8 bytes short of a block, and the length in bits
        padded = self._pending + b"\x80" + bytes((55 - len(self._pending)) % 64)
        padded += (self._length * 8 % 2**64).to_bytes(8, order)
        state = self._state
        for start in range(0, len(padded), 64):
            state = block_function(state, padded[start:start + 64])
        return b"".join(word.to_bytes(4, order) for word in state)

    def hexdigest(self):
        """Return the digest value as a string of hexadecimal digits."""
        return self.digest().hex()

    def copy(self):
        """Return a copy of the hash object."""
        return new_hash(self._name, self._state, self._pending, self._length)

    def __repr__(self):
        return f"<{self._name} _hashlib.HASH object @ {hex(id(self))}>"


def new_hash(name, state=None, pending=b"", length=0):
    made = object.__new__(HASH)
    made._name = name
    made._state = list(ALGORITHMS[name][0] if state is None else state)
    made._pending, made._length = pending, length
    return made


@model(hashlib)
class _model_hashlib:
    # the copy of OpenSSL's hash objects
    HASH = HASH

    @model(hashlib.md5)
    def md5(string=b"", *, usedforsecurity=True):
        # an md5 hash object, fed string
        made = new_hash("md5")
        made.update(string)
        return made

    @model(hashlib.sha1)
    def sha1(string=b"", *, usedforsecurity=True):
        # a sha1 hash object, fed string
        made = new_hash("sha1")
        made.update(string)
        return made
