# the model of binascii: base64 encoding, which binascii does in C, written in plain Python. The rest of the
# module isn't modelled

import binascii

from MagicF import model

BASE64_ALPHABET = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"


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
