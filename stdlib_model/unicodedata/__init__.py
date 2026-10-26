# the model of unicodedata: normalize(), whose answers come from the Unicode database, which isn't modelled: ASCII
# text is in every normal form, and other text is normalized to a magic value. The rest of the module isn't
# modelled

import unicodedata

from MagicF import constraint, magic, model

FORMS = ("NFC", "NFKC", "NFD", "NFKD")


@model(unicodedata)
class _model_unicodedata:
    @model(unicodedata.normalize)
    def normalize(form, unistr, /):
        # unistr in the normal form: as it is when it's ASCII, otherwise not empty when it isn't
        for position, argument in enumerate((form, unistr), 1):
            if not isinstance(argument, str):
                raise TypeError(f"normalize() argument {position} must be str, not {type(argument).__name__}")
        if unistr == "":
            return unistr  # before the form is checked, as CPython does
        if form not in FORMS:
            raise ValueError("invalid normalization form")
        if unistr.isascii():
            return unistr
        normalized = magic(str)
        constraint(normalized != "")
        return normalized
