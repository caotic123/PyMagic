# the model of httplib2.decode: the decoders of compressed content
# (the copies are from httplib2 0.32, MIT license, Copyright 2006 Joe Gregorio)

import zlib
from typing import Protocol

import httplib2.decode

from MagicF import constraint, magic, model


# ------------------------------------------------------------------ copied from httplib2

@model(httplib2.decode.DecodeRatioError)
class DecodeRatioError(Exception):
    """Output-to-input amplification ratio exceeded the configured limit."""

@model(httplib2.decode.DecodeLimitError)
class DecodeLimitError(Exception):
    """Total output length exceeded the hard limit."""


@model(httplib2.decode.DecoderProtocol)
class DecoderProtocol(Protocol):
    @property
    def needs_input(self) -> bool:
        ...

    def decode(self, b: bytes) -> bytes:
        ...

    def flush(self) -> bytes:
        ...

    def consume_bytes(self, data: bytes, chunk_size: int = 64 << 10) -> bytes:
        out = bytearray()
        if chunk_size == 0:
            chunk_size = len(data)
        for i in range(0, len(data), chunk_size):
            chunk = data[i : i + chunk_size]
            out.extend(self.decode(chunk))
        out.extend(self.flush())
        return bytes(out)


@model(httplib2.decode.ZlibDecoder)
class ZlibDecoder(DecoderProtocol):
    """
    Thin wrapper around zlib.Decompressor conforming to the Decoder interface.

    Note: zlib pushes all available decompressed data immediately upon receiving
    input. It never holds back output requiring `decode(b"")` to extract it.
    Thus, `needs_input` naturally remains True.
    """

    __slots__ = ("_decoder",)

    WBITS_DEFLATE = -15
    WBITS_ZLIB = 15
    WBITS_GZIP = 15 | 16
    WBITS_AUTO_GZIP_ZLIB = 15 | 32  # but not deflate

    def __init__(self, wbits: int = WBITS_AUTO_GZIP_ZLIB):
        self._decoder: zlib._Decompress | None = zlib.decompressobj(wbits)

    @property
    def needs_input(self) -> bool:
        if self._decoder is None:
            raise RuntimeError("used after flush()")
        return not self._decoder.eof

    def decode(self, b: bytes) -> bytes:
        if self._decoder is None:
            raise RuntimeError("used after flush()")
        return self._decoder.decompress(b)

    def flush(self) -> bytes:
        if self._decoder is None:
            raise RuntimeError("used after flush()")
        result = self._decoder.flush()
        self._decoder = None
        return result


def DeflateDecoder() -> ZlibDecoder:
    return ZlibDecoder(ZlibDecoder.WBITS_DEFLATE)


@model(httplib2.decode.LimitDecoder)
class LimitDecoder(DecoderProtocol):
    __slots__ = (
        "_decoder",
        "_ratio",
        "_chunk_size",
        "_safe_limit",
        "_hard_limit",
        "_consumed_length",
        "_output_length",
        "_input_buffer",
        "_flushed",
    )

    def __init__(
        self,
        decoder: DecoderProtocol,
        ratio: float = 100,
        chunk_size: int = 64 << 10,
        safe_limit: int = 10 << 20,
        hard_limit: int = 10 << 30,
    ) -> None:
        if ratio < 0:
            raise ValueError(f"LimitDecoder() ratio={ratio} expected >= 0")
        if chunk_size < 0:
            raise ValueError(f"LimitDecoder() chunk_size={chunk_size} expected >= 0")
        if safe_limit < 0:
            raise ValueError(f"LimitDecoder() safe_limit={safe_limit} expected >= 0")
        if hard_limit < 0:
            raise ValueError(f"LimitDecoder() safe_limit={safe_limit} expected >= 0")

        self._decoder: DecoderProtocol = decoder
        self._ratio: float = ratio
        self._chunk_size: int = chunk_size
        self._safe_limit: int = safe_limit
        self._hard_limit: int = hard_limit
        self._consumed_length: int = 0
        self._output_length: int = 0
        self._input_buffer: bytearray = bytearray()
        self._flushed: bool = False

    def _check_limits(self) -> None:
        if (self._hard_limit > 0) and (self._output_length > self._hard_limit):
            raise DecodeLimitError(f"Output length {self._output_length} exceeds hard limit {self._hard_limit}")
        if (self._safe_limit > 0) and (self._output_length < self._safe_limit):
            return
        if (self._ratio > 0) and (self._output_length > self._consumed_length * self._ratio):
            actual_ratio = self._output_length / self._consumed_length if self._consumed_length > 0 else float("inf")
            raise DecodeRatioError(
                f"Amplification ratio {actual_ratio:.1f} ({self._output_length}/{self._consumed_length})"
                f" exceeds limit {self._ratio}"
            )

    @property
    def needs_input(self) -> bool:
        return self._decoder.needs_input

    def decode(self, b: bytes) -> bytes:
        if self._flushed:
            raise RuntimeError("decode() called after flush()")
        output = self._pump(b)
        return bytes(output)

    def flush(self) -> bytes:
        if self._flushed:
            raise RuntimeError("flush() called more than once")
        self._flushed = True

        output = self._pump(b"")

        data = self._decoder.flush()
        output.extend(data)
        self._output_length += len(data)
        self._check_limits()

        return bytes(output)

    def _pump(self, b: bytes) -> bytearray:
        self._input_buffer.extend(b)

        output = bytearray()
        while True:
            if not self._decoder.needs_input:
                data = self._decoder.decode(b"")
                if data:
                    output.extend(data)
                    self._output_length += len(data)
                    self._check_limits()
                    continue

            if self._input_buffer:
                chunk = bytes(self._input_buffer[: self._chunk_size])
                del self._input_buffer[: self._chunk_size]

                data = self._decoder.decode(chunk)
                self._consumed_length += len(chunk)

                if data:
                    output.extend(data)
                    self._output_length += len(data)
                    self._check_limits()

                continue

            # neither input nor decoder progress
            break

        return output


# ------------------------------------------------------------------ the model

def raw_deflate(data):
    # data as a raw deflate stream of one last stored block: its length, the length's complement, then data
    return b"\x01" + len(data).to_bytes(2, "little") + (len(data) ^ 0xFFFF).to_bytes(2, "little") + data

def decodes(decoder, data, decoded):
    # whether a copy of the decoder turns data into decoded, leaving the decoder as it was
    try:
        return decoder._decoder.copy().decompress(data) == decoded
    except zlib.error:
        return False


@model(httplib2.decode)
class _model_decode:
    # the copies of its classes
    DecodeRatioError = DecodeRatioError
    DecodeLimitError = DecodeLimitError
    DecoderProtocol = DecoderProtocol
    ZlibDecoder = ZlibDecoder
    LimitDecoder = LimitDecoder

    @model(httplib2.decode.DeflateDecoder)
    def DeflateDecoder():
        # a fresh decoder of raw deflate streams, with no zlib or gzip header
        decoder = magic(ZlibDecoder)
        constraint(decoder._decoder is not None and not decoder._decoder.eof)
        constraint(decodes(decoder, raw_deflate(b"deflated"), b"deflated"))
        return decoder
