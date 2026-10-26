import gzip
import zlib
import random

import pytest

from PyMagic import RunningUnstatedModel
from stdlib_model.testing import modelled, outcome


def decompressed(stream, wbits, chunks=None, zdict=b""):
    # what a decompressor makes of the stream fed in chunks, and its state at the end
    made = zlib.decompressobj(wbits, zdict=zdict) if zdict else zlib.decompressobj(wbits)
    out = b"".join(made.decompress(chunk) for chunk in (chunks or [stream]))
    copied = made.copy()
    return out + made.flush(), made.eof, made.unused_data, made.unconsumed_tail, copied.decompress(b"more"), \
        copied.unused_data

def compressed(data, wbits, pick):
    level = pick.choice([0, 1, 6, 9])
    strategy = pick.choice([zlib.Z_DEFAULT_STRATEGY, zlib.Z_FIXED, zlib.Z_HUFFMAN_ONLY, zlib.Z_RLE])
    compressor = zlib.compressobj(level, zlib.DEFLATED, wbits, 8, strategy)
    return compressor.compress(data) + compressor.flush()

def sample(pick):
    size = pick.choice([0, 1, 5, 100, 1000])
    kind = pick.choice(["random", "text", "repeated"])
    if kind == "random":
        return bytes(pick.randrange(256) for _ in range(size))
    if kind == "text":
        return bytes(pick.choice(b"abc de\n") for _ in range(size))
    return b"xyz" * size

def test_inflates_as_zlib():
    pick = random.Random(3)
    for _ in range(150):
        data = sample(pick)
        wbits = pick.choice([-15, -9, 15, 9, 31, 47, 0, 16, 32])
        stream = compressed(data, {0: 15, 16: 31, 32: 15, 47: pick.choice([15, 31])}.get(wbits, wbits), pick)
        variant = pick.choice(["whole", "truncated", "corrupted", "tail", "chunked"])
        if variant == "truncated" and stream:
            stream = stream[:pick.randrange(len(stream))]
        if variant == "corrupted" and stream:
            corrupted = bytearray(stream)
            corrupted[pick.randrange(len(corrupted))] ^= 1 << pick.randrange(8)
            stream = bytes(corrupted)
        if variant == "tail":
            stream += b"tail"
        step = 7 if len(stream) < 200 else 97
        chunks = [stream[i:i + step] for i in range(0, len(stream), step)] if variant == "chunked" else None
        call = lambda: decompressed(stream, wbits, chunks)
        assert modelled(call) == outcome(call), (variant, wbits, stream)

def header(gzip_flags=0, method=8):
    return bytes([0x1F, 0x8B, method, gzip_flags]) + bytes(6)

STREAMS = {
    "garbage": (b"garbage", 15),
    "bad block type": (b"\x78\x9c\xff", 15),
    "raw bad block type": (b"\x07\x00", -15),
    "gzip header only": (b"\x1f\x8b\x08\x00", 31),
    "gzip reserved flags": (header(0xE0) + b"\x00" * 10, 31),
    "gzip method": (header(method=7) + b"\x00" * 10, 31),
    "gzip extra name comment hcrc": (bytes(gzip.compress(b"hi", mtime=0)[:3]) + bytes([0x1E]) + bytes(6) +
                                     b"\x02\x00ab" + b"name\x00" + b"comment\x00" + b"\x00\x00" +
                                     gzip.compress(b"hi", mtime=0)[10:], 31),
    "zlib small window": (zlib.compress(b"hi"), 9),
    "zlib window 0": (zlib.compress(b"hi"), 0),
    "auto gzip": (gzip.compress(b"hi", mtime=0), 47),
    "auto zlib": (zlib.compress(b"hi"), 47),
    "one byte auto": (b"\x1f", 47),
    "stored": (b"\x01\x02\x00\xfd\xffhi", -15),
    "stored lengths": (b"\x01\x02\x00\xfd\xfehi", -15),
    "after end": (zlib.compress(b"hi") + b"tail", 15),
}

@pytest.mark.parametrize("stream, wbits", STREAMS.values(), ids=STREAMS.keys())
def test_streams_as_zlib(stream, wbits):
    call = lambda: decompressed(stream, wbits)
    assert modelled(call) == outcome(call)

@pytest.mark.parametrize("damage", ["data check", "length check", "adler", "header crc"])
def test_trailers_as_zlib(damage):
    stream, wbits = bytearray(gzip.compress(b"hello", mtime=0)), 31
    if damage == "data check":
        stream[-5] ^= 1
    elif damage == "length check":
        stream[-1] ^= 1
    elif damage == "adler":
        stream, wbits = bytearray(zlib.compress(b"hello")), 15
        stream[-1] ^= 1
    else:
        stream[3] |= 0x02
        stream[10:10] = b"\x00\x00"
    call = lambda: decompressed(bytes(stream), wbits)
    assert modelled(call) == outcome(call)

def test_preset_dictionaries_as_zlib():
    compressor = zlib.compressobj(zdict=b"hello world")
    stream = compressor.compress(b"hello world hello") + compressor.flush()
    for zdict in (b"hello world", b"other"):
        call = lambda: decompressed(stream, 15, zdict=zdict)
        assert modelled(call) == outcome(call)
    call = lambda: decompressed(stream, 15)
    assert modelled(call) == outcome(call)
    raw = zlib.compressobj(wbits=-15, zdict=b"abcabc")
    stream = raw.compress(b"abcabcabc") + raw.flush()
    call = lambda: decompressed(stream, -15, zdict=b"abcabc")
    assert modelled(call) == outcome(call)

@pytest.mark.parametrize("wbits", range(-20, 50))
def test_window_bits_as_zlib(wbits):
    call = lambda: type(zlib.decompressobj(wbits)).__name__
    assert modelled(call) == outcome(call)

@pytest.mark.parametrize("call", [
    lambda: zlib.decompressobj().decompress("text"),
    lambda: zlib.decompressobj(zdict="text"),
    lambda: zlib.decompressobj().decompress(b"", -1),
    lambda: zlib.decompressobj().flush(0),
    lambda: zlib.decompressobj().decompress(b"x" * 3) == b"",
], ids=["str data", "str zdict", "negative max_length", "flush length 0", "nothing yet"])
def test_refuses_as_zlib(call):
    assert modelled(call) == outcome(call)

def test_max_length_is_not_modelled():
    with pytest.raises(RunningUnstatedModel, match="max_length"):
        modelled(lambda: zlib.decompressobj().decompress(zlib.compress(b"x" * 100), 10))
