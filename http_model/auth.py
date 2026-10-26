# the model of httplib2.auth: the WWW-Authenticate and Authentication-Info headers, which httplib2 parses with
# a pyparsing grammar, parsed here in plain Python as that grammar does (RFC 7235). A challenge is a scheme and
# either comma-separated auth-params or a token68, and a header lists challenges between commas. As with
# pyparsing, tabs are expanded, whitespace may go between any two parts, and what follows the longest list
# that parses is left out
# (the parse is httplib2 0.32's, MIT license, Copyright 2006 Joe Gregorio)

import string

from .error import MalformedHeader

WHITESPACE = " \n\t\r"
TOKEN_CHARACTERS = frozenset("!#$%&'*+-.^_`|~" + string.digits + string.ascii_letters)
TOKEN68_CHARACTERS = frozenset("-._~+/" + string.digits + string.ascii_letters)
HEXDIGITS = frozenset(string.hexdigits)


def skipped(text, at):
    while at < len(text) and text[at] in WHITESPACE:
        at += 1
    return at

def run_of(text, at, characters):
    # where the run of characters starting at at ends
    while at < len(text) and text[at] in characters:
        at += 1
    return at

def literal(text, at, character):
    # where the character ends, if it comes after any whitespace
    at = skipped(text, at)
    return at + 1 if text.startswith(character, at) else None

def token(text, at):
    # (the token after any whitespace, where it ends), or None
    at = skipped(text, at)
    end = run_of(text, at, TOKEN_CHARACTERS)
    return None if end == at else (text[at:end], end)

def token68(text, at):
    # (the token68 after any whitespace: its characters and any "=" padding, where it ends), or None
    at = skipped(text, at)
    end = run_of(text, at, TOKEN68_CHARACTERS)
    return None if end == at else (text[at:run_of(text, end, "=")], run_of(text, end, "="))

def quoted_end(text, at):
    # where a quoted string opened at at stops, taking characters as pyparsing's regular expression does, each
    # the first way it can: any but a quote, a line break or a backslash; two quotes; a backslash with any
    # character but x; or a backslash, an x and hexadecimal digits
    end = at + 1
    while end < len(text):
        if text[end] not in '"\n\r\\':
            end += 1
        elif text.startswith('""', end):
            end += 2
        elif text[end] == "\\" and text[end + 1:end + 2] not in ("", "x"):
            end += 2
        elif text.startswith("\\x", end) and text[end + 2:end + 3] in HEXDIGITS:
            end = run_of(text, end + 2, HEXDIGITS)
        else:
            break
    return end

def unquoted(text):
    # each backslash dropped from before the character it escapes, which isn't a line break
    characters, at = [], 0
    while at < len(text):
        escaped = text[at] == "\\" and text[at + 1:at + 2] not in ("", "\n")
        characters.append(text[at + escaped])
        at += 1 + escaped
    return "".join(characters)

def quoted_string(text, at):
    at = skipped(text, at)
    if not text.startswith('"', at):
        return None
    end = quoted_end(text, at)
    if not text.startswith('"', end):
        return None
    return unquoted(text[at + 1:end]), end + 1

def auth_param(text, at):
    # name=value, with the name in lowercase and the value a token or a quoted string
    name = token(text, at)
    equals = None if name is None else literal(text, name[1], "=")
    value = None if equals is None else quoted_string(text, equals) or token(text, equals)
    return None if value is None else ((name[0].lower(), value[0]), value[1])

def delimited(item, text, at):
    # one or more items between commas, as many as parse: (the items, where they end), or None
    found = item(text, at)
    if found is None:
        return None
    items, at = [found[0]], found[1]
    while (comma := literal(text, at, ",")) is not None and (found := item(text, comma)) is not None:
        items.append(found[0])
        at = found[1]
    return items, at

def auth_params(text, at):
    found = delimited(auth_param, text, at)
    return None if found is None else (dict(found[0]), found[1])

def challenge(text, at):
    # (the scheme in lowercase, its params or {"token": its token68}), where it ends
    scheme = token(text, at)
    if scheme is None:
        return None
    params = auth_params(text, scheme[1])
    if params is not None:
        return (scheme[0].lower(), params[0]), params[1]
    found = token68(text, scheme[1])
    return None if found is None else ((scheme[0].lower(), {"token": found[0]}), found[1])


def _parse_authentication_info(headers, headername="authentication-info"):
    """https://tools.ietf.org/html/rfc7615
    """
    header = headers.get(headername, "").strip()
    if not header:
        return {}
    params = auth_params(header.expandtabs(), 0)
    if params is None:
        raise MalformedHeader(headername)
    return params[0]


def _parse_www_authenticate(headers, headername="www-authenticate"):
    """Returns a dictionary of dictionaries, one dict per auth_scheme."""
    header = headers.get(headername, "").strip()
    if not header:
        return {}
    challenges = delimited(challenge, header.expandtabs(), 0)
    if challenges is None:
        raise MalformedHeader(headername)
    return dict(challenges[0])
