import random

import pytest

from PyMagic import BrokenVariant
from stdlib_model.magics import stdlib_magics
from stdlib_model.testing import modelled, outcome

REFUSED = [(0,), (-3,), (5, 5), (10, 0), (0, 9, 0), (0, 10, -1), (1.5,), (0, 2.5), (0, 10, 1.5), ("a",), (3, None, 2)]

@pytest.mark.filterwarnings("ignore::DeprecationWarning")
@pytest.mark.parametrize("args", REFUSED, ids=repr)
def test_randrange_refuses_as_random(args):
    call = lambda: random.randrange(*args)
    assert modelled(call) == outcome(call)

DRAWN = [(10,), (0, 10), (-5, 5), (0, 10, 3), (10, 0, -2), (2.0,), (0, 2**70)]

@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_randrange_draws_as_a_seeded_generator():
    calls = lambda: [random.randrange(*args) for args in DRAWN]
    chance = random.Random(7)
    assert modelled(calls, stdlib_magics(seed=7)) == ("returned", [chance.randrange(*args) for args in DRAWN])

@pytest.mark.parametrize("args, drawn", [((0, 9), 9), ((0, 9), -1), ((0, 10, 3), 4), ((5,), 5)], ids=repr)
def test_randrange_rejects_a_value_outside_its_range(args, drawn):
    with pytest.raises(BrokenVariant):
        modelled(lambda: random.randrange(*args), {random: {random.randrange: lambda *_: [drawn]}})
