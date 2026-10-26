# the model of zlib: decompressobj(), which zlib does in C, written in plain Python (RFC 1950, 1951 and 1952),
# and a copy of zlib.error, which stands in for it in a run. The decompressor keeps its input and inflates it
# again from the start as more comes, so what it gives at each call is what zlib gives. Compression, checksums as
# functions and the one-shot decompress() aren't modelled
# (the inflating follows Mark Adler's puff.c and zlib's inflate.c, and keeps zlib's error messages)

import zlib

from MagicF import RunningUnstatedModel, model

MAX_WBITS = 15
DEF_BUF_SIZE = 16384


@model(zlib.error)
class error(Exception):
    pass


def data_error(reason):
    return error(f"Error -3 while decompressing data: {reason}")


# ------------------------------------------------------------------ checksums

def crc_of_byte(value):
    for _ in range(8):
        value = 0xEDB88320 ^ (value >> 1) if value & 1 else value >> 1
    return value

CRC_TABLE = [crc_of_byte(value) for value in range(256)]

def crc32(data):
    value = 0xFFFFFFFF
    for byte in data:
        value = CRC_TABLE[(value ^ byte) & 0xFF] ^ (value >> 8)
    return value ^ 0xFFFFFFFF

def adler32(data):
    low, high = 1, 0
    for byte in data:
        low = (low + byte) % 65521
        high = (high + low) % 65521
    return high << 16 | low


# ------------------------------------------------------------------ inflating

class Truncated(Exception):
    # the input ran out before the stream ended: not an error, zlib waits for more
    pass


class Bits:
    # the input read bit by bit, the least significant bit of each byte first, as deflate packs it
    def __init__(self, data):
        self.data, self.at, self.buffer, self.count = data, 0, 0, 0

    def take(self, n):
        while self.count < n:
            if self.at >= len(self.data):
                raise Truncated
            self.buffer |= self.data[self.at] << self.count
            self.at += 1
            self.count += 8
        value = self.buffer & ((1 << n) - 1)
        self.buffer >>= n
        self.count -= n
        return value

    def align(self):
        # drop what is left of the current byte
        self.take(self.count % 8)

    def number(self, size, order):
        # a number of size whole bytes, read once aligned
        return int.from_bytes(bytes(self.take(8) for _ in range(size)), order)

    def position(self):
        # where the unread input starts, after aligning
        return self.at - self.count // 8


class Huffman:
    # a canonical Huffman code from the length of each symbol's code, decoded as puff.c does
    def __init__(self, lengths):
        self.counts = [0] * 16
        for length in lengths:
            self.counts[length] += 1
        offsets = [0, 0]
        for length in range(1, 15):
            offsets.append(offsets[length] + self.counts[length])
        self.symbols = [0] * len(lengths)
        for symbol, length in enumerate(lengths):
            if length:
                self.symbols[offsets[length]] = symbol
                offsets[length] += 1
        self.max_length = max((length for length in range(16) if self.counts[length]), default=0)

    def left(self):
        # the codes left over: 0 for a complete code, more when incomplete, less when over-subscribed
        left = 1
        for length in range(1, 16):
            left = left * 2 - self.counts[length]
            if left < 0:
                return left
        return left

    def decode(self, bits):
        # the next symbol, or None at a code the lengths leave unused
        code = first = index = 0
        for length in range(1, 16):
            code |= bits.take(1)
            count = self.counts[length]
            if code - count < first:
                return self.symbols[index + code - first]
            index += count
            first = (first + count) << 1
            code <<= 1
        return None

def usable(lengths, what):
    # the Huffman code of lengths, refused as zlib's inflate_table refuses it
    code = Huffman(lengths)
    left = code.left()
    if code.max_length and (left < 0 or (left > 0 and (what == "code lengths" or code.max_length != 1))):
        raise data_error(f"invalid {what} set")
    return code

LENGTH_BASES = [3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31, 35, 43, 51, 59, 67, 83, 99, 115, 131, 163,
                195, 227, 258]
LENGTH_EXTRA_BITS = [0] * 8 + [1] * 4 + [2] * 4 + [3] * 4 + [4] * 4 + [5] * 4 + [0]
DISTANCE_BASES = [1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193, 257, 385, 513, 769, 1025, 1537, 2049,
                  3073, 4097, 6145, 8193, 12289, 16385, 24577]
DISTANCE_EXTRA_BITS = [0, 0] + [bits for bits in range(14) for _ in range(2)]
CODE_LENGTH_ORDER = [16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15]

FIXED_LENGTHS = Huffman([8] * 144 + [9] * 112 + [7] * 24 + [8] * 8)
FIXED_DISTANCES = Huffman([5] * 30)

def stored(bits, out):
    bits.align()
    length, complement = bits.take(16), bits.take(16)
    if length != complement ^ 0xFFFF:
        raise data_error("invalid stored block lengths")
    for _ in range(length):
        out.append(bits.take(8))

def dynamic(bits):
    # the codes of a dynamic block, from the code lengths at its start
    literals, distances, code_lengths = bits.take(5) + 257, bits.take(5) + 1, bits.take(4) + 4
    if literals > 286 or distances > 30:
        raise data_error("too many length or distance symbols")
    lengths = [0] * 19
    for position in CODE_LENGTH_ORDER[:code_lengths]:
        lengths[position] = bits.take(3)
    lengths_code = usable(lengths, "code lengths")
    lengths = []
    while len(lengths) < literals + distances:
        symbol = lengths_code.decode(bits)
        if symbol is None:
            raise data_error("invalid code lengths set")
        if symbol < 16:
            lengths.append(symbol)
            continue
        if symbol == 16:
            if not lengths:
                raise data_error("invalid bit length repeat")
            repeated, times = lengths[-1], 3 + bits.take(2)
        else:
            repeated, times = 0, (3 + bits.take(3)) if symbol == 17 else (11 + bits.take(7))
        if len(lengths) + times > literals + distances:
            raise data_error("invalid bit length repeat")
        lengths += [repeated] * times
    if lengths[256] == 0:
        raise data_error("invalid code -- missing end-of-block")
    return usable(lengths[:literals], "literal/lengths"), usable(lengths[literals:], "distances")

def codes(bits, out, history, literal_code, distance_code):
    # the literals and copies of a block, up to its end
    while True:
        symbol = literal_code.decode(bits)
        if symbol is None or symbol > 285:
            raise data_error("invalid literal/length code")
        if symbol < 256:
            out.append(symbol)
            continue
        if symbol == 256:
            return
        length = LENGTH_BASES[symbol - 257] + bits.take(LENGTH_EXTRA_BITS[symbol - 257])
        symbol = distance_code.decode(bits)
        if symbol is None or symbol > 29:
            raise data_error("invalid distance code")
        distance = DISTANCE_BASES[symbol] + bits.take(DISTANCE_EXTRA_BITS[symbol])
        if distance > len(history) + len(out):
            raise data_error("invalid distance too far back")
        for _ in range(length):
            out.append(out[-distance] if distance <= len(out) else history[len(out) - distance])

def inflate(bits, out, history):
    # the blocks of a deflate stream into out, up to the last one
    while True:
        last, kind = bits.take(1), bits.take(2)
        if kind == 0:
            stored(bits, out)
        elif kind == 1:
            codes(bits, out, history, FIXED_LENGTHS, FIXED_DISTANCES)
        elif kind == 2:
            codes(bits, out, history, *dynamic(bits))
        else:
            raise data_error("invalid block type")
        if last:
            return

def zlib_header(bits, window, zdict):
    # the dictionary the stream's zlib header asks for, if any, after checking the header
    header = bits.take(8) << 8 | bits.take(8)
    if header % 31:
        raise data_error("incorrect header check")
    if header >> 8 & 0x0F != 8:
        raise data_error("unknown compression method")
    needed = (header >> 12) + 8
    if needed > 15 or needed > (window or needed):
        raise data_error("invalid window size")
    if not header & 0x20:
        return b""
    dictionary = bits.number(4, "big")
    if not zdict:
        raise error("Error 2 while decompressing data")
    if dictionary != adler32(zdict):
        raise error("Error -3 while setting zdict: invalid input data")
    return zdict

def gzip_header(bits):
    header = bytearray()
    def read(size):
        header.extend(bits.take(8) for _ in range(size))
        return header[-size:]
    if read(2) != b"\x1f\x8b":
        raise data_error("incorrect header check")
    if read(1)[0] != 8:
        raise data_error("unknown compression method")
    flags = read(1)[0]
    if flags & 0xE0:
        raise data_error("unknown header flags set")
    read(6)  # the modification time, the extra flags and the operating system
    if flags & 0x04:
        read(int.from_bytes(read(2), "little"))
    for flag in (0x08, 0x10):  # the file name and the comment, each ending with a zero
        while flags & flag and read(1)[0]:
            pass
    if flags & 0x02 and bits.number(2, "little") != crc32(header) & 0xFFFF:
        raise data_error("header crc mismatch")

def inflated(data, wbits, zdict):
    # (what the stream in data inflates to so far, where it ends or None while it hasn't)
    out = bytearray()
    bits = Bits(data)
    try:
        if wbits < 0:
            inflate(bits, out, zdict)
            bits.align()
            return out, bits.position()
        wrap, window = wbits >> 4, wbits & 0x0F
        if wrap == 2 and len(data) < 2:
            raise Truncated
        if wrap == 1 or (wrap == 2 and data[:2] == b"\x1f\x8b"):
            gzip_header(bits)
            inflate(bits, out, b"")
            bits.align()
            if bits.number(4, "little") != crc32(out):
                raise data_error("incorrect data check")
            if bits.number(4, "little") != len(out) % 2**32:
                raise data_error("incorrect length check")
        else:
            inflate(bits, out, zlib_header(bits, window, zdict))
            bits.align()
            if bits.number(4, "big") != adler32(out):
                raise data_error("incorrect data check")
        return out, bits.position()
    except Truncated:
        return out, None


def bytes_of(data, message):
    try:
        return memoryview(data).tobytes()
    except TypeError:
        raise TypeError(message) from None


class Decompress:
    # zlib's Decompress, which zlib doesn't name: what decompressobj() makes
    def __init__(self, wbits, zdict):
        self._wbits, self._zdict, self._input, self._given = wbits, zdict, b"", 0
        self.eof, self.unused_data, self.unconsumed_tail = False, b"", b""

    def decompress(self, data, /, max_length=0):
        data = bytes_of(data, f"a bytes-like object is required, not '{type(data).__name__}'")
        if max_length < 0:
            raise ValueError("max_length must be non-negative")
        if max_length:
            raise RunningUnstatedModel("the zlib model doesn't model decompress() with a max_length")
        if self.eof:
            self.unused_data += data
            return b""
        self._input += data
        out, end = inflated(self._input, self._wbits, self._zdict)
        new, self._given = bytes(out[self._given:]), len(out)
        if end is not None:
            self.eof, self.unused_data = True, self._input[end:]
        return new

    def flush(self, length=DEF_BUF_SIZE, /):
        # all there is to give is given at each decompress()
        if length <= 0:
            raise ValueError("length must be greater than zero")
        return b""

    def copy(self):
        copied = Decompress(self._wbits, self._zdict)
        copied._input, copied._given = self._input, self._given
        copied.eof, copied.unused_data, copied.unconsumed_tail = self.eof, self.unused_data, self.unconsumed_tail
        return copied

    def __copy__(self):
        return self.copy()

    def __deepcopy__(self, memo, /):
        return self.copy()


@model(zlib)
class _model_zlib:
    # the copy of its error
    error = error

    @model(zlib.decompressobj)
    def decompressobj(wbits=MAX_WBITS, zdict=b""):
        # a decompressor of raw deflate (wbits -8 to -15), zlib (0, 8 to 15), gzip (16 more) or either (32 more)
        zdict = bytes_of(zdict, "zdict argument must support the buffer protocol")
        window = -wbits if wbits < 0 else wbits & 0x0F
        if not (-15 <= wbits < 48 and (window == 0 and wbits >= 0 or 8 <= window <= 15)):
            raise ValueError("Invalid initialization option")
        return Decompress(wbits, zdict)
