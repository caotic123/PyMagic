import time

import cachetools
import pytest

from PyMagic import BrokenVariant, run_model
from cachetools_model import models
from stdlib_model.time import MONOTONIC


def clock(*readings):
    # the monotonic clock reads these seconds, one reading after the other
    MONOTONIC[0] = 0.0
    reading = iter(readings)
    return {time: {time.monotonic: lambda: reading}}

def session(maxsize, ttl, steps, timer=None):
    # what a TTLCache answers to each step: ("set", key, value), ("get", key), ("in", key), ("len",) or ("keys",)
    cache = cachetools.TTLCache(maxsize, ttl) if timer is None else cachetools.TTLCache(maxsize, ttl, timer=timer)
    answers = []
    for step in steps:
        if step[0] == "set":
            cache[step[1]] = step[2]
            answers.append(None)
        elif step[0] == "get":
            answers.append(cache.get(step[1], "missing"))
        elif step[0] == "in":
            answers.append(step[1] in cache)
        elif step[0] == "len":
            answers.append(len(cache))
        else:
            answers.append(sorted(cache))
    return answers

def modelled(maxsize, ttl, steps, readings):
    return run_model(models, clock(*readings))(lambda: session(maxsize, ttl, steps))

def real(maxsize, ttl, steps, readings):
    reading = iter(readings)
    return session(maxsize, ttl, steps, timer=lambda: next(reading))

STEPS = [("set", "a", 1), ("get", "a"), ("set", "b", 2), ("set", "c", 3), ("len",), ("keys",), ("in", "a"),
         ("get", "b"), ("get", "c"), ("len",)]

SESSIONS = {
    "nothing expires": [0.0] * 40,
    "everything expires": [0.0, 1.0, 2.0, 3.0, 4.0] + [100.0] * 40,
    "a second each reading": [float(second) for second in range(40)],
    "expiry at the reading itself": [0.0, 0.0, 0.0, 0.0, 10.0] + [10.0] * 40,
}

@pytest.mark.parametrize("readings", SESSIONS.values(), ids=SESSIONS.keys())
def test_ttl_cache_as_cachetools(readings):
    assert modelled(2, 10, STEPS, readings) == real(2, 10, STEPS, readings)

def test_the_clock_never_goes_back():
    with pytest.raises(BrokenVariant):
        modelled(2, 10, STEPS, [5.0, 4.0] + [4.0] * 40)

def test_the_clock_reads_floats():
    with pytest.raises(BrokenVariant):
        modelled(2, 10, STEPS, [1] * 40)
