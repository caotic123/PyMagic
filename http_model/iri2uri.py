# the model of httplib2.iri2uri: IRIs as URIs
# (the copies are from httplib2 0.32, MIT license, Copyright 2006 Joe Gregorio)

import importlib
import urllib.parse

from MagicF import constraint, magic, model
from stdlib_model.encodings import idna

IRI2URI = importlib.import_module("httplib2.iri2uri")  # httplib2.iri2uri is the function, hiding the module


# ------------------------------------------------------------------ copied from httplib2

# the ucschar and iprivate of RFC 3987: the characters IRIs allow and URIs don't
escape_range = [
    (0xA0, 0xD7FF),
    (0xE000, 0xF8FF),
    (0xF900, 0xFDCF),
    (0xFDF0, 0xFFEF),
    (0x10000, 0x1FFFD),
    (0x20000, 0x2FFFD),
    (0x30000, 0x3FFFD),
    (0x40000, 0x4FFFD),
    (0x50000, 0x5FFFD),
    (0x60000, 0x6FFFD),
    (0x70000, 0x7FFFD),
    (0x80000, 0x8FFFD),
    (0x90000, 0x9FFFD),
    (0xA0000, 0xAFFFD),
    (0xB0000, 0xBFFFD),
    (0xC0000, 0xCFFFD),
    (0xD0000, 0xDFFFD),
    (0xE1000, 0xEFFFD),
    (0xF0000, 0xFFFFD),
    (0x100000, 0x10FFFD),
]


def encode(c):
    retval = c
    i = ord(c)
    for low, high in escape_range:
        if i < low:
            break
        if i >= low and i <= high:
            retval = "".join(["%%%2X" % o for o in c.encode("utf-8")])
            break
    return retval


def iri2uri(uri):
    """Convert an IRI to a URI. Note that IRIs must be
    passed in a unicode strings. That is, do not utf-8 encode
    the IRI before passing it into the function."""
    if isinstance(uri, str):
        (scheme, authority, path, query, fragment) = urllib.parse.urlsplit(uri)
        authority = idna.encoded(authority).decode("utf-8")  # authority.encode("idna")
        # For each character in 'ucschar' or 'iprivate'
        #  1. encode as utf-8
        #  2. then %-encode each octet of that utf-8
        uri = urllib.parse.urlunsplit((scheme, authority, path, query, fragment))
        uri = "".join([encode(c) for c in uri])
    return uri


# ------------------------------------------------------------------ the model

def as_uri_character(character):
    # a character IRIs allow and URIs don't goes into a URI as the percent-encoded octets of its UTF-8
    if any(low <= ord(character) <= high for low, high in escape_range):
        return "".join(f"%{octet:02X}" for octet in character.encode("utf-8"))
    return character


@model(IRI2URI)
class _model_iri2uri:
    @model(IRI2URI.iri2uri)
    def iri2uri(uri):
        # an IRI as a URI: its host in IDNA and the characters only IRIs allow percent-encoded; anything but a
        # str is left as it is
        if not isinstance(uri, str):
            return uri
        scheme, authority, path, query, fragment = urllib.parse.urlsplit(uri)  # a bad IPv6 raises ValueError
        host = idna.encoded(authority).decode("utf-8")  # authority.encode("idna")
        iri = urllib.parse.urlunsplit((scheme, host, path, query, fragment))
        converted = magic(str)
        constraint(converted == "".join(map(as_uri_character, iri)))
        return converted
