# the model of encodings.idna: the codec of str.encode("idna"). nameprep() needs the Unicode database, which isn't
# modelled, so what it makes of a label is magic, or the UnicodeError it raises; the rest is copied, with the
# labels split at their dots without a regular expression. A copy of Codec stands in for it in a run, but
# str.encode("idna") keeps the codec registered at startup, so the models use this Codec
# (the copies are from CPython 3.10, Python Software Foundation License)

import codecs
import encodings.idna
import encodings.punycode  # loaded now rather than by the codec registry in a run
from encodings.idna import nameprep  # modelled: in a run, its model

from MagicF import constraint, magic, model

# IDNA section 3.1
DOTS = ".。．｡"

# IDNA section 5
ace_prefix = b"xn--"
sace_prefix = "xn--"


def split_dots(text):
    # dots.split(text) of the copied module: the labels between any of the dots
    labels, label = [], []
    for character in text:
        if character in DOTS:
            labels.append("".join(label))
            label = []
        else:
            label.append(character)
    return labels + ["".join(label)]


# ------------------------------------------------------------------ copied from encodings.idna

def ToASCII(label):
    try:
        # Step 1: try ASCII
        label = label.encode("ascii")
    except UnicodeError:
        pass
    else:
        # Skip to step 3: UseSTD3ASCIIRules is false, so
        # Skip to step 8.
        if 0 < len(label) < 64:
            return label
        raise UnicodeError("label empty or too long")

    # Step 2: nameprep
    label = nameprep(label)

    # Step 3: UseSTD3ASCIIRules is false
    # Step 4: try ASCII
    try:
        label = label.encode("ascii")
    except UnicodeError:
        pass
    else:
        # Skip to step 8.
        if 0 < len(label) < 64:
            return label
        raise UnicodeError("label empty or too long")

    # Step 5: Check ACE prefix
    if label.startswith(sace_prefix):
        raise UnicodeError("Label starts with ACE prefix")

    # Step 6: Encode with PUNYCODE
    label = label.encode("punycode")

    # Step 7: Prepend ACE prefix
    label = ace_prefix + label

    # Step 8: Check size
    if 0 < len(label) < 64:
        return label
    raise UnicodeError("label empty or too long")

def ToUnicode(label):
    # Step 1: Check for ASCII
    if isinstance(label, bytes):
        pure_ascii = True
    else:
        try:
            label = label.encode("ascii")
            pure_ascii = True
        except UnicodeError:
            pure_ascii = False
    if not pure_ascii:
        # Step 2: Perform nameprep
        label = nameprep(label)
        # It doesn't say this, but apparently, it should be ASCII now
        try:
            label = label.encode("ascii")
        except UnicodeError:
            raise UnicodeError("Invalid character in IDN label")
    # Step 3: Check for ACE prefix
    if not label.startswith(ace_prefix):
        return str(label, "ascii")

    # Step 4: Remove ACE prefix
    label1 = label[len(ace_prefix):]

    # Step 5: Decode using PUNYCODE
    result = label1.decode("punycode")

    # Step 6: Apply ToASCII
    label2 = ToASCII(result)

    # Step 7: Compare the result of step 6 with the one of step 3
    # label2 will already be in lower case.
    if str(label, "ascii").lower() != str(label2, "ascii"):
        raise UnicodeError("IDNA does not round-trip", label, label2)

    # Step 8: return the result of step 5
    return result

### Codec APIs

@model(encodings.idna.Codec)
class Codec(codecs.Codec):
    def encode(self, input, errors='strict'):

        if errors != 'strict':
            # IDNA is quite clear that implementations must be strict
            raise UnicodeError("unsupported error handling "+errors)

        if not input:
            return b'', 0

        try:
            result = input.encode('ascii')
        except UnicodeEncodeError:
            pass
        else:
            # ASCII name: fast path
            labels = result.split(b'.')
            for label in labels[:-1]:
                if not (0 < len(label) < 64):
                    raise UnicodeError("label empty or too long")
            if len(labels[-1]) >= 64:
                raise UnicodeError("label too long")
            return result, len(input)

        result = bytearray()
        labels = split_dots(input)
        if labels and not labels[-1]:
            trailing_dot = b'.'
            del labels[-1]
        else:
            trailing_dot = b''
        for label in labels:
            if result:
                # Join with U+002E
                result.extend(b'.')
            result.extend(ToASCII(label))
        return bytes(result+trailing_dot), len(input)

    def decode(self, input, errors='strict'):

        if errors != 'strict':
            raise UnicodeError("Unsupported error handling "+errors)

        if not input:
            return "", 0

        # IDNA allows decoding to operate on Unicode strings, too.
        if not isinstance(input, bytes):
            # XXX obviously wrong, see #3232
            input = bytes(input)

        if ace_prefix not in input:
            # Fast path
            try:
                return input.decode('ascii'), len(input)
            except UnicodeDecodeError:
                pass

        labels = input.split(b".")

        if labels and len(labels[-1]) == 0:
            trailing_dot = '.'
            del labels[-1]
        else:
            trailing_dot = ''

        result = []
        for label in labels:
            result.append(ToUnicode(label))

        return ".".join(result)+trailing_dot, len(input)


def encoded(text):
    # text.encode("idna"): what this Codec makes of it, with its UnicodeError wrapped as str.encode() wraps a
    # codec's error
    try:
        return Codec().encode(text)[0]
    except UnicodeError as error:
        if type(error) is not UnicodeError:
            raise
        raise UnicodeError(f"encoding with 'idna' codec failed (UnicodeError: {error})") from error


# ------------------------------------------------------------------ the model

@model(encodings.idna)
class _model_idna:
    # the copy of its codec
    Codec = Codec

    @model(encodings.idna.nameprep)
    def nameprep(label):
        # the label mapped, normalized and checked by stringprep's nameprep profile (RFC 3491): magic, as the
        # Unicode database isn't modelled, or the UnicodeError of a label it refuses; it lowercases ASCII letters
        prepared = magic(str, UnicodeError)
        if isinstance(prepared, UnicodeError):
            raise prepared
        constraint(not any("A" <= character <= "Z" for character in prepared))
        return prepared
