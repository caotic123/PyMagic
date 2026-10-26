import re
import random

import pytest

from stdlib_model.re import expand_template, parse_template
from stdlib_model.testing import each, modelled, modelled_each, outcome

PATTERNS = {
    "no groups": (re.compile(r"(?:\r\n)?[ \t]+"), 0, {}),
    "one group": (re.compile(r"( )"), 1, {}),
    "a named group": (re.compile(r"(?P<space> )"), 1, {"space": 1}),
}
PIECES = ["a", "\\", "g", "<", ">", "0", "1", "2", "7", "8", "9", "n", "d", "x", "-", "é", " ", "+", "_", "space"]

def templates(count, seed=5):
    pick = random.Random(seed)
    return ["a\\nb", "a\\g<0>", "\\g< 1>", "a\\", "\\g<space>"] + \
        ["".join(pick.choice(PIECES) for _ in range(pick.randrange(1, 9))) for _ in range(count)]

def failure(call):
    # the outcome, with re.error's attributes
    def caught():
        try:
            return call()
        except re.error as error:
            return "raised", error.msg, error.pattern, error.pos, error.lineno, error.colno
    return caught

@pytest.mark.parametrize("pattern, groups, groupindex", PATTERNS.values(), ids=PATTERNS.keys())
def test_expands_templates_as_re(pattern, groups, groupindex):
    def expanded(template):
        return failure(lambda: expand_template(parse_template(template, groups, groupindex), [" "] * (groups + 1)))
    every = templates(2000)
    assert modelled_each(map(expanded, every)) == each(failure(lambda t=t: pattern.sub(t, " ")) for t in every)

def test_error_tells_lines_and_columns_as_re():
    call = failure(lambda: parse_template("a\nb\\d", 0, {}))
    assert modelled(call) == outcome(failure(lambda: re.compile(" ").sub("a\nb\\d", " ")))
