# the model of re: a copy of re.error, which stands in for it in a run, and the replacement templates of sub(),
# parsed and expanded as sre_parse does. Matching isn't modelled: the models match text by hand
# (the copies are from CPython 3.10, Python Software Foundation License)

import re

from MagicF import model

MAXGROUPS = 2147483647  # sre_constants.MAXGROUPS

DIGITS = frozenset("0123456789")
OCTDIGITS = frozenset("01234567")
ASCIILETTERS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")

ESCAPES = {r"\a": "\a", r"\b": "\b", r"\f": "\f", r"\n": "\n", r"\r": "\r", r"\t": "\t", r"\v": "\v", r"\\": "\\"}


# ------------------------------------------------------------------ copied from sre_constants and sre_parse

@model(re.error)
class error(Exception):
    """Exception raised for invalid regular expressions.

    Attributes:

        msg: The unformatted error message
        pattern: The regular expression pattern
        pos: The index in the pattern where compilation failed (may be None)
        lineno: The line corresponding to pos (may be None)
        colno: The column corresponding to pos (may be None)
    """

    __module__ = 're'

    def __init__(self, msg, pattern=None, pos=None):
        self.msg = msg
        self.pattern = pattern
        self.pos = pos
        if pattern is not None and pos is not None:
            msg = '%s at position %d' % (msg, pos)
            if isinstance(pattern, str):
                newline = '\n'
            else:
                newline = b'\n'
            self.lineno = pattern.count(newline, 0, pos) + 1
            self.colno = pos - pattern.rfind(newline, 0, pos)
            if newline in pattern:
                msg = '%s (line %d, column %d)' % (msg, self.lineno, self.colno)
        else:
            self.lineno = self.colno = None
        super().__init__(msg)


class Tokenizer:
    def __init__(self, string):
        self.istext = isinstance(string, str)
        self.string = string
        if not self.istext:
            string = str(string, 'latin1')
        self.decoded_string = string
        self.index = 0
        self.next = None
        self.__next()
    def __next(self):
        index = self.index
        try:
            char = self.decoded_string[index]
        except IndexError:
            self.next = None
            return
        if char == "\\":
            index += 1
            try:
                char += self.decoded_string[index]
            except IndexError:
                raise error("bad escape (end of pattern)",
                            self.string, len(self.string) - 1) from None
        self.index = index + 1
        self.next = char
    def match(self, char):
        if char == self.next:
            self.__next()
            return True
        return False
    def get(self):
        this = self.next
        self.__next()
        return this
    def getuntil(self, terminator, name):
        result = ''
        while True:
            c = self.next
            self.__next()
            if c is None:
                if not result:
                    raise self.error("missing " + name)
                raise self.error("missing %s, unterminated name" % terminator,
                                 len(result))
            if c == terminator:
                if not result:
                    raise self.error("missing " + name, 1)
                break
            result += c
        return result
    def tell(self):
        return self.index - len(self.next or '')
    def error(self, msg, offset=0):
        return error(msg, self.string, self.tell() - offset)


def parse_template(source, groups, groupindex):
    # sre_parse.parse_template, for a pattern of groups groups named as groupindex says: (the group of each
    # reference, the literals with None where they go)
    s = Tokenizer(source)
    sget = s.get
    references = []
    literals = []
    literal = []
    lappend = literal.append
    def addgroup(index, pos):
        if index > groups:
            raise s.error("invalid group reference %d" % index, pos)
        if literal:
            literals.append(''.join(literal))
            del literal[:]
        references.append((len(literals), index))
        literals.append(None)
    while True:
        this = sget()
        if this is None:
            break # end of replacement string
        if this[0] == "\\":
            # group
            c = this[1]
            if c == "g":
                name = ""
                if not s.match("<"):
                    raise s.error("missing <")
                name = s.getuntil(">", "group name")
                if name.isidentifier():
                    try:
                        index = groupindex[name]
                    except KeyError:
                        raise IndexError("unknown group name %r" % name)
                else:
                    try:
                        index = int(name)
                        if index < 0:
                            raise ValueError
                    except ValueError:
                        raise s.error("bad character in group name %r" % name,
                                      len(name) + 1) from None
                    if index >= MAXGROUPS:
                        raise s.error("invalid group reference %d" % index,
                                      len(name) + 1)
                addgroup(index, len(name) + 1)
            elif c == "0":
                if s.next in OCTDIGITS:
                    this += sget()
                    if s.next in OCTDIGITS:
                        this += sget()
                lappend(chr(int(this[1:], 8) & 0xff))
            elif c in DIGITS:
                isoctal = False
                if s.next in DIGITS:
                    this += sget()
                    if (c in OCTDIGITS and this[2] in OCTDIGITS and
                        s.next in OCTDIGITS):
                        this += sget()
                        isoctal = True
                        c = int(this[1:], 8)
                        if c > 0o377:
                            raise s.error('octal escape value %s outside of '
                                          'range 0-0o377' % this, len(this))
                        lappend(chr(c))
                if not isoctal:
                    addgroup(int(this[1:]), len(this) - 1)
            else:
                try:
                    this = ESCAPES[this]
                except KeyError:
                    if c in ASCIILETTERS:
                        raise s.error('bad escape %s' % this, len(this))
                lappend(this)
        else:
            lappend(this)
    if literal:
        literals.append(''.join(literal))
    return references, literals


def expand_template(template, match):
    # sre_parse.expand_template, with the match as its groups' texts, group 0 first and None when unmatched
    references, literals = template
    literals = literals[:]
    for index, group in references:
        literals[index] = match[group] or ""
    return "".join(literals)


# ------------------------------------------------------------------ the model

@model(re)
class _model_re:
    # the copy of its error
    error = error
