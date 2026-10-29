# the model of binascii: base64 encoding and decoding, which binascii does in C, written in plain Python. The rest
# of the module isn't modelled

import binascii

from PyMagic import model, named

BASE64_ALPHABET = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"

# the value of each base64 character; decoding skips the others
BASE64_VALUES = {character: value for value, character in enumerate(BASE64_ALPHABET)}


# binascii.Error, which the model raises as it is: in a run it's the original, which the functions it doesn't model
# raise too, and compiled it's a class that stands in for it
Error = named(binascii.Error)


def ascii_data(data):
    # data as binascii takes ASCII in: an ASCII str, or a contiguous bytes-like object
    if isinstance(data, str):
        if not data.isascii():
            raise ValueError("string argument should contain only ASCII characters")
        return data.encode("ascii")
    try:
        view = memoryview(data)
    except TypeError:
        view = None
    if view is None or not view.c_contiguous:
        raise TypeError(f"argument should be bytes, buffer or ASCII string, not '{type(data).__name__}'")
    return view.tobytes()


@model(binascii)
class _model_binascii:
    @model(binascii.b2a_base64)
    def b2a_base64(data, /, *, newline=True):
        # data in base64, each 3 bytes as 4 characters and the last 1 or 2 padded with "=", then a newline
        try:
            data = memoryview(data).tobytes()
        except TypeError:
            raise TypeError(f"a bytes-like object is required, not '{type(data).__name__}'") from None
        encoded = bytearray()
        for start in range(0, len(data), 3):
            chunk = data[start:start + 3]
            bits = int.from_bytes(chunk + bytes(3 - len(chunk)), "big")
            sextets = [bits >> shift & 0x3F for shift in (18, 12, 6, 0)]
            encoded += bytes(BASE64_ALPHABET[sextet] for sextet in sextets[:len(chunk) + 1])
            encoded += b"=" * (3 - len(chunk))
        if newline:
            encoded += b"\n"
        return bytes(encoded)

    @model(binascii.a2b_base64)
    def a2b_base64(data, /):
        # base64 decoded as binascii decodes it, leniently: each 4 characters as 3 bytes, skipping the characters
        # out of the alphabet; a pad ends the input once it completes a quad of which 2 characters are in
        decoded, in_quad, left, pads = bytearray(), 0, 0, 0
        for character in ascii_data(data):
            if character == ord("="):
                if in_quad >= 2:
                    pads += 1
                    if in_quad + pads >= 4:
                        return bytes(decoded)
                continue
            value = BASE64_VALUES.get(character)
            if value is None:
                continue
            pads = 0
            if in_quad == 1:
                decoded.append(left << 2 | value >> 4)
            elif in_quad == 2:
                decoded.append((left << 4 | value >> 2) & 0xFF)
            elif in_quad == 3:
                decoded.append((left << 6 | value) & 0xFF)
            left = value & (0x3F, 0x0F, 0x03, 0)[in_quad]
            in_quad = (in_quad + 1) % 4
        if in_quad == 1:
            raise Error(f"Invalid base64-encoded string: number of data characters ({len(decoded) // 3 * 4 + 1}) "
                        f"cannot be 1 more than a multiple of 4")
        if in_quad:
            raise Error("Incorrect padding")
        return bytes(decoded)
