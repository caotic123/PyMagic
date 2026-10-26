import unicodedata

import pytest

from PyMagic import BrokenVariant, run_model
from stdlib_model import models
from stdlib_model.testing import modelled, outcome

@pytest.mark.parametrize("form", ["NFC", "NFKC", "NFD", "NFKD", "XYZ", 5])
@pytest.mark.parametrize("text", ["", "plain ascii", "café", "ﬁ", "Å", "ẛ̣", b"bytes"],
                         ids=repr)
def test_normalize_as_the_unicode_database(form, text):
    call = lambda: unicodedata.normalize(form, text)
    assert modelled(call) == outcome(call)

def test_ascii_needs_no_magic():
    assert run_model(models)(lambda: unicodedata.normalize("NFKC", "abc")) == "abc"

def test_normalize_rejects_nothing_for_something():
    with pytest.raises(BrokenVariant):
        modelled(lambda: unicodedata.normalize("NFC", "café"), {unicodedata: {unicodedata.normalize: lambda *_: [""]}})
