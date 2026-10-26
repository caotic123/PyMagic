import time
import random

import pytest

from MagicF import BrokenVariant, RunningUnstatedModel
from stdlib_model.magics import stdlib_magics
from stdlib_model.testing import NOW, each, modelled, modelled_each, outcome
from stdlib_model.time import is_ctime


def broken_down(t):
    return repr(t), tuple(t), t.tm_zone, t.tm_gmtoff

SECONDS = [0, 1, -1, 59, 86399, 86400, -86401, 951782400, NOW, 2**31, -2**31, 10**15, 253402300799, 1.9, -1.5,
           10**17, -10**17, float("nan"), float("inf"), 1e20, 2**63, -2**63 - 1, "x", True]

@pytest.mark.parametrize("seconds", SECONDS, ids=repr)
def test_gmtime_as_glibc(seconds):
    call = lambda: broken_down(time.gmtime(seconds))
    assert modelled(call) == outcome(call)

def test_gmtime_of_random_seconds_as_glibc():
    pick = random.Random(4)
    calls = [lambda s=pick.randrange(-10**12, 10**12): broken_down(time.gmtime(s)) for _ in range(300)]
    assert modelled_each(calls) == each(calls)

def test_gmtime_reads_the_clock():
    assert modelled(lambda: broken_down(time.gmtime())) == outcome(lambda: broken_down(time.gmtime(NOW)))
    with pytest.raises(BrokenVariant):
        modelled(lambda: time.gmtime(), {time: {time.gmtime: lambda: [2**64]}})

STRUCTS = {
    "nine": lambda: time.struct_time(range(9)),
    "eight": lambda: time.struct_time((1,) * 8),
    "eleven": lambda: time.struct_time((1,) * 11),
    "twelve": lambda: time.struct_time((1,) * 12),
    "dict": lambda: time.struct_time(range(9), {"tm_zone": "X", "tm_gmtoff": 5}),
    "not a dict": lambda: time.struct_time(range(9), 5),
    "not a sequence": lambda: time.struct_time(5),
    "reduce": lambda: time.gmtime(0).__reduce__()[1],
    "readonly": lambda: setattr(time.gmtime(0), "tm_year", 1),
    "no attribute": lambda: setattr(time.gmtime(0), "other", 1),
    "fields": lambda: (time.gmtime(0).tm_yday, time.struct_time.n_fields, time.struct_time.__match_args__),
}

@pytest.mark.parametrize("call", STRUCTS.values(), ids=STRUCTS.keys())
def test_struct_time_as_time(call):
    made = lambda: (lambda value: broken_down(value) if hasattr(value, "tm_zone") else value)(call())
    assert modelled(made) == outcome(made)

FORMATS = ["%Y-%m-%dT%H:%M:%SZ", "%a %A %b %B %c", "%d %e %H %I %j %m %M %p %S %u %w %y %Y %z",
           "%D %F %R %r %T %x %X %h %n %t %%", "plain%", "%Z"]

@pytest.mark.parametrize("format", FORMATS)
def test_strftime_as_glibc(format):
    pick = random.Random(5)
    seconds = [pick.randrange(-10**10, 10**11) for _ in range(100)]
    calls = [lambda s=s: time.strftime(format, time.gmtime(s)) for s in seconds]
    if "%Z" not in format:
        calls += [lambda t=tuple(time.gmtime(s)): time.strftime(format, t) for s in seconds]
    assert modelled_each(calls) == each(calls)

TUPLES = {
    "month 13": (2020, 13, 1, 0, 0, 0, 0, 1, 0), "month 0": (2020, 0, 1, 0, 0, 0, 0, 1, 0),
    "day 0": (2020, 1, 0, 0, 0, 0, 0, 1, 0), "day 32": (2020, 1, 32, 0, 0, 0, 0, 1, 0),
    "yday 0": (2020, 1, 1, 0, 0, 0, 0, 0, 0), "yday 367": (2020, 1, 1, 0, 0, 0, 0, 367, 0),
    "wday -1": (2020, 1, 1, 0, 0, 0, -1, 1, 0), "hour 24": (2020, 1, 1, 24, 0, 0, 0, 1, 0),
    "minute 60": (2020, 1, 1, 0, 60, 0, 0, 1, 0), "seconds 61": (2020, 1, 1, 0, 0, 61, 0, 1, 0),
    "seconds 62": (2020, 1, 1, 0, 0, 62, 0, 1, 0), "eight items": (2020, 1, 1, 0, 0, 0, 0, 1),
    "a list": [2020] * 9, "strings": ("a",) * 9, "big year": (2**40, 1, 1, 0, 0, 0, 0, 1, 0),
    "year 0": (0, 1, 1, 0, 0, 0, 0, 1, 0), "year -5": (-5, 1, 1, 0, 0, 0, 0, 1, 0),
    "year 12345": (12345, 1, 1, 0, 0, 0, 0, 1, 0),
}

@pytest.mark.parametrize("t", TUPLES.values(), ids=TUPLES.keys())
def test_strftime_checks_as_time(t):
    call = lambda: time.strftime("%Y %y %m %d %j %a %H:%M:%S %z", t)
    assert modelled(call) == outcome(call)

@pytest.mark.parametrize("call", [lambda: time.strftime(5, time.gmtime(0)), lambda: time.strftime("a\0b", (0,) * 9)],
                         ids=["format not str", "null character"])
def test_strftime_refuses_as_time(call):
    assert modelled(call) == outcome(call)

@pytest.mark.parametrize("call", [lambda: time.strftime("%C", time.gmtime(0)), lambda: time.strftime("%Z", (0,) * 9),
                                  lambda: time.strftime("%Y")],
                         ids=["directive not modelled", "zone of a plain tuple", "local time"])
def test_strftime_says_what_it_does_not_model(call):
    with pytest.raises(RunningUnstatedModel):
        modelled(call)

def test_ctime_has_the_form_of_ctime():
    pick = random.Random(6)
    for seconds in [0, -1, 10**10, *(pick.randrange(-10**11, 10**11) for _ in range(200))]:
        assert is_ctime(time.ctime(seconds)), time.ctime(seconds)
    for text in ["", "Mon Jan  1 00:00:00", "Mon Jan 01 00:00:00 2020", "Mon Jan  1 24:00:00 2020",
                 "Xyz Jan  1 00:00:00 2020", "Mon Jan  1 00:00:00 02020", "Mon Jan 32 00:00:00 2020"]:
        assert not is_ctime(text), text

@pytest.mark.parametrize("seconds", [None, 0, NOW, 1.9, "x", float("nan"), 1e20], ids=repr)
def test_ctime_reads_the_clock_and_timezone(seconds):
    call = lambda: time.ctime() if seconds is None else time.ctime(seconds)
    assert modelled(call) == outcome(lambda: time.ctime(NOW if seconds is None else seconds))

def test_ctime_rejects_text_not_of_its_form():
    with pytest.raises(BrokenVariant):
        modelled(lambda: time.ctime(), {time: {time.ctime: lambda: ["yesterday"]}})
