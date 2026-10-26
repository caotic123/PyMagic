import encodings.idna

import pytest

from PyMagic import BrokenVariant
from stdlib_model.encodings import idna
from stdlib_model.testing import modelled, outcome

HOSTS = ["example.com", "", ".", "a..b", "x" * 63, "x" * 64, "a." + "x" * 64, "Bücher.example", "☃.net",
         "a。b．c｡d", "אa.example", "אב.example", "xn--abc.é", "é" * 60,
         "trailing.dot.", "ÉCOLE.fr"]

def errors_as_text(call):
    # the outcome, with an error's cause too, as str.encode() chains the codec's error
    def caught():
        try:
            return call()
        except UnicodeError as error:
            return "raised", type(error), str(error), type(error.__cause__), error.__suppress_context__
    return caught

@pytest.mark.parametrize("host", HOSTS, ids=repr)
def test_encodes_as_str_encode(host):
    assert modelled(errors_as_text(lambda: idna.encoded(host))) == outcome(errors_as_text(lambda: host.encode("idna")))

@pytest.mark.parametrize("host", HOSTS, ids=repr)
def test_codec_as_the_idna_codec(host):
    encode = lambda codec: codec.encode(host)
    assert modelled(lambda: encode(idna.Codec())) == outcome(lambda: encode(encodings.idna.Codec()))
    encoded = outcome(lambda: encodings.idna.Codec().encode(host))
    if encoded[0] == "returned":
        decode = lambda codec: codec.decode(encoded[1][0])
        assert modelled(lambda: decode(idna.Codec())) == outcome(lambda: decode(encodings.idna.Codec()))

def test_nameprep_keeps_no_uppercase_letters():
    nameprep = {encodings.idna: {encodings.idna.nameprep: lambda label: ["Bücher"]}}
    with pytest.raises(BrokenVariant):
        modelled(lambda: idna.encoded("Bücher.example"), nameprep)
