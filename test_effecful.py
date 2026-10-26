import sys
import random
import builtins
import itertools
from random import randint
from unittest import mock

import pytest

import MagicF
from MagicF import (
    BrokenVariant, MissedModelObject, RunningUnstatedModel, UncompilableModel,
    Compilation, Model, collect_magic, compile_models, constraint, get_models, magic, model, run_model,
)
from effecful import magics, magics_by_name, models, simple, _model_Random

ORIGINAL_RANDINT = random.randint
THIS_MODULE = sys.modules[__name__]


def pair(a, b):
    return a, b


@model(THIS_MODULE)
class _model_pair:
    @model(pair)
    def model_pair(a, b):
        x = magic()
        y = magic()
        return (x, y)


def roll(n):
    return list(range(n))


@model(THIS_MODULE)
class _model_roll:
    @model(roll)
    def model_roll(n):
        rolls = []
        x = magic(int)
        rolls.append(x)
        x = magic(int)
        rolls.append(x)
        for _ in range(n):
            x = magic(int)
            rolls.append(x)
        return rolls


class Dog:
    def speak(self):
        return "woof"


@model(Dog)
class _model_Dog:
    def speak(self):
        return "meow"


class Box:
    def __init__(self):
        self.value = 1


@model(Box)
class _model_Box:
    @model(Box.__init__)
    def __init__(self):
        self.value = magic(int)


def take(obj):
    return obj


@model(THIS_MODULE)
class _model_take:
    @model(take)
    def model_take(obj):
        x = magic()
        return x


@model(THIS_MODULE)
class _model_max:
    @model(max)
    def model_max(*args):
        x = magic()
        constraint(x in args)
        return x


def inc(a):
    return a + 1


@model(THIS_MODULE)
class _model_inc:
    @model(inc)
    def model_inc(a):
        text = """
at column zero
"""
        x = magic() + 1
        values = [magic()]
        y = z = magic()
        return [x, values[0], y, z]


calls_seen = 0


def count():
    return 0


@model(THIS_MODULE)
class _model_count:
    @model(count)
    def model_count():
        global calls_seen
        calls_seen += 1
        return calls_seen


class BadRepr:
    def __repr__(self):
        raise RuntimeError("repr exploded")


@model(pair)
def model_pair_alone(a, b):
    x = magic()
    y = magic()
    return (x, y)


def divide(a, b):
    return divmod(a, b)


@model(divide)
def model_divide(a, b):
    q = magic(int)
    r = magic(int)
    constraint(q * b + r == a and 0 <= r < b)
    return (q, r)


def exists(path):
    return True


@model(exists)
def model_exists(path):
    x = magic(bool)
    return x


def ping():
    return "pong"


@model(ping)
def model_ping():
    return "pong"


LIMIT = 100


def cap(value):
    return min(value, LIMIT)


@model(cap)
def model_cap(value):
    x = magic(int)
    constraint(x <= LIMIT)
    return x


def dice(n):
    return [random.randint(1, 6) for _ in range(n)]


@model(dice)
def model_dice(n):
    rolls = []
    for _ in range(n):
        x = magic(int)
        constraint(1 <= x <= 6)
        rolls.append(x)
    return rolls


class NotFound(Exception):
    pass


def request(url):
    return 200


@model(request)
def model_request(url):
    status_code = magic(int)
    constraint(100 <= status_code <= 599)
    if status_code == 404:
        raise NotFound(f"{url} was not found")
    return status_code


def read_line():
    return "text"


@model(read_line)
def model_read_line():
    x = magic()
    if not isinstance(x, str):
        raise TypeError(f"The value {x!r} is a {type(x).__name__}, but it should be a string")
    return x


@model(random.randint)
def model_randint_with_empty_range(a, b):
    if a > b:
        raise ValueError(f"empty range for randint({a}, {b})")
    x = magic(int)
    constraint(a <= x <= b)
    return x


def identifier(name):
    return name


@model(identifier)
def model_identifier(name):
    y = magic(str, int)
    return y


# models of the compiler review: each one hit a hole in concrete compilation

def number():
    return 5


@model(number)
def model_number_reassigned():
    x = magic(int)
    x = x + 1
    return x


@model(number)
def model_number_in_branch():
    if True:
        x = magic(int)
    return x


@model(number)
def model_number_drawing_twice():
    x = magic()
    y = magic()
    return x + y


@model(number)
def model_number_qualified():
    x = MagicF.magic(int)
    MagicF.constraint(x > 0)
    return x


def triple():
    return (1, 2, 3)


@model(triple)
def model_triple_as_pair():
    q = magic(int)
    r = magic(int)
    if q < 0:
        raise ValueError("negative")
    return (q, r)


def negative():
    return -1


@model(negative)
def model_negative_falling_off():
    x = magic(int)
    if x > 0:
        return x


def two():
    return [1, 2]


@model(two)
def model_two_drawing_three():
    values = [magic(), magic(), magic()]
    return values


def make_not_found():
    return NotFound("made")


def fetch_missing():
    raise KeyError("boom")


@model(fetch_missing)
def model_fetch_missing():
    x = magic()
    if x is None:
        raise make_not_found()
    return x


DEFAULT_MODE = "r"


class Path(str):
    pass


def write(path, mode="r"):
    return mode


@model(write)
def model_write(path: Path, mode=DEFAULT_MODE):
    x = magic(str)
    return x


@model(write)
def model_write_other_default(path, mode="w"):
    x = magic(str)
    return x


def both_randints():
    return random.randint(1, 6), randint(1, 6)


# ------------------------------------------------------------------ running models

def test_model_links_originals():
    assert _model_Random.__model__ is random
    assert _model_Random.model_randomint.__model__ == random.randint


def test_collect_magic():
    assert collect_magic(_model_Random.model_randomint) == ["x"]
    assert collect_magic(model_divide) == ["q", "r"]


def test_magics_patch_module_and_from_imports():
    assert run_model(models, magics)(both_randints) == (3, 3)


def test_patches_are_restored():
    run_model(models, magics)(both_randints)
    assert random.randint is ORIGINAL_RANDINT
    assert randint is ORIGINAL_RANDINT
    assert 1 <= random.randint(1, 6) <= 6


def test_generator_gets_call_arguments():
    seen = []
    generator = lambda min, max: seen.append((min, max)) or [min]
    run_model(models, {random: {random.randint: generator}})(lambda: random.randint(10, 20))
    assert seen == [(10, 20)]


def test_generator_values_by_name():
    assert run_model(models, magics_by_name)(both_randints) == (6, 6)


def test_each_magic_draws_the_next_value():
    drawing = {THIS_MODULE: {pair: lambda a, b: itertools.cycle([a + b, a * b])}}
    assert run_model([_model_pair], drawing)(lambda: pair(2, 5)) == (7, 10)


def test_cycle_repeats_values():
    repeating = {THIS_MODULE: {pair: lambda a, b: itertools.cycle([a])}}
    assert run_model([_model_pair], repeating)(lambda: pair(4, 9)) == (4, 4)


def test_each_variable_by_name():
    by_name = {THIS_MODULE: {pair: lambda a, b: {"y": [a], "x": [b]}}}
    assert run_model([_model_pair], by_name)(lambda: pair(4, 9)) == (9, 4)


def test_same_variable_twice_and_in_a_loop_draws_by_name():
    by_name = {THIS_MODULE: {roll: lambda n: {"x": iter([10, 20, 30, 40, 50])}}}
    assert run_model([_model_roll], by_name)(lambda: roll(3)) == [10, 20, 30, 40, 50]


def test_cycle_by_name():
    by_name = {THIS_MODULE: {roll: lambda n: {"x": itertools.cycle([n, -n])}}}
    assert run_model([_model_roll], by_name)(lambda: roll(2)) == [2, -2, 2, -2]


def test_running_out_of_values_by_name():
    two_values = {THIS_MODULE: {roll: lambda n: {"x": [1, 2]}}}
    with pytest.raises(RunningUnstatedModel, match=r"ran out of magic values at x = magic\(int\)"):
        run_model([_model_roll], two_values)(lambda: roll(1))


def test_value_by_name_must_be_iterable():
    scalar = {THIS_MODULE: {pair: lambda a, b: {"x": 1, "y": [2]}}}
    with pytest.raises(RunningUnstatedModel, match=r"gave 'x': 1; each variable needs an iterable"):
        run_model([_model_pair], scalar)(lambda: pair(4, 9))


def test_missing_variable_name():
    only_x = {THIS_MODULE: {pair: lambda a, b: {"x": [a]}}}
    with pytest.raises(RunningUnstatedModel, match="no values for 'y'"):
        run_model([_model_pair], only_x)(lambda: pair(4, 9))


def test_broken_constraint_is_a_broken_variant():
    out_of_range = {random: {random.randint: lambda min, max: [max + 1]}}
    with pytest.raises(BrokenVariant, match=r"drew \[7\] and broke constraint\(min <= x <= max\) .*rejected this call"):
        run_model(models, out_of_range)(both_randints)


def test_magic_of_the_wrong_kind_is_a_broken_variant():
    text = {random: {random.randint: lambda min, max: ["3"]}}
    with pytest.raises(BrokenVariant, match=r"drew '3' at x = magic\(int\) .*which is not int"):
        run_model(models, text)(both_randints)


def test_constraint_outside_a_model():
    constraint(1 < 2)
    with pytest.raises(BrokenVariant, match=r"constraint\(1 > 2\) .*failed outside any running model"):
        constraint(1 > 2)


def test_running_out_of_values():
    one_value = {THIS_MODULE: {pair: lambda a, b: [a]}}
    with pytest.raises(RunningUnstatedModel, match=r"ran out of magic values at y = magic\(\)"):
        run_model([_model_pair], one_value)(lambda: pair(4, 9))


def test_generator_must_return_dict_or_iterable():
    not_iterable = {random: {random.randint: lambda min, max: 3}}
    with pytest.raises(RunningUnstatedModel, match="returned 3; it must return"):
        run_model(models, not_iterable)(both_randints)


def test_unknown_variable_name():
    unknown = {random: {random.randint: lambda min, max: {"y": 1}}}
    with pytest.raises(RunningUnstatedModel, match=r"\['y'\]"):
        run_model(models, unknown)(both_randints)


def test_magics_for_unmodelled_function():
    unmodelled = {random: {random.choice: lambda seq: [seq[0]]}}
    with pytest.raises(MissedModelObject, match="random.choice"):
        run_model(models, unmodelled)(both_randints)


def test_without_magics_magic_is_unstated():
    with pytest.raises(RunningUnstatedModel, match=r"reached x = magic\(int\) .*no magics were given"):
        run_model(models)(both_randints)


def test_call_must_match_original_signature():
    with pytest.raises(TypeError, match="does not match the signature"):
        run_model(models, magics)(lambda: random.randint(1, 2, 3))


def test_magic_outside_model():
    with pytest.raises(RunningUnstatedModel, match=r"magic\(int\) was called with no model state"):
        magic(int)


def test_model_needs_original():
    with pytest.raises(MissedModelObject, match="got None"):
        model(None)


def test_model_decorates_only_classes_and_functions():
    with pytest.raises(MissedModelObject, match="got 42 of type int"):
        model(random.randint)(42)


def test_class_model_patches_class_and_existing_instances():
    dog = Dog()
    assert run_model([_model_Dog])(lambda: (dog.speak(), Dog().speak())) == ("meow", "meow")
    assert dog.speak() == "woof"


def test_patches_are_restored_when_target_raises():
    def boom():
        raise ValueError("target failed")
    with pytest.raises(ValueError, match="target failed"):
        run_model(models, magics)(boom)
    assert random.randint is ORIGINAL_RANDINT
    assert randint is ORIGINAL_RANDINT


def test_collect_magic_lists_each_variable_once():
    assert collect_magic(_model_roll.model_roll) == ["x"]
    assert collect_magic(_model_inc.model_inc) == []


def test_model_returning_none_is_valid():
    box_magics = {Box: {Box.__init__: lambda self: [7]}}
    assert run_model([_model_Box], box_magics)(lambda: Box().value) == 7
    assert Box().value == 1


def test_model_returning_zero_is_valid():
    assert run_model([_model_take], {THIS_MODULE: {take: lambda obj: [0]}})(lambda: take("anything")) == 0


def test_arguments_are_not_repr_unless_there_is_an_error():
    assert run_model([_model_take], {THIS_MODULE: {take: lambda obj: [5]}})(lambda: take(BadRepr())) == 5


def test_model_of_builtin_without_signature():
    assert run_model([_model_max], {THIS_MODULE: {max: lambda *args: [2]}})(lambda: max(1, 2)) == 2


def test_every_magic_call_draws_from_an_iterable():
    # magic() in an expression, in a list and in a chained assignment; the string at column zero is fine too
    drawing = {THIS_MODULE: {inc: lambda a: iter([a, 20, 30])}}
    assert run_model([_model_inc], drawing)(lambda: inc(10)) == [11, 20, 30, 30]


def test_magic_without_a_variable_cannot_be_set_by_name():
    by_name = {THIS_MODULE: {inc: lambda a: {}}}
    with pytest.raises(RunningUnstatedModel, match=r"reached magic\(\) .*not assigned straight to a variable"):
        run_model([_model_inc], by_name)(lambda: inc(10))


def test_model_keeps_its_closure_and_globals():
    limit = 10

    @model(THIS_MODULE)
    class _model_limited:
        @model(take)
        def model_take(obj):
            x = magic()
            constraint(x < limit)
            return x

    under = {THIS_MODULE: {take: lambda obj: [5]}}
    assert run_model([_model_limited], under)(lambda: take(None)) == 5

    before = calls_seen
    run_model([_model_count], {THIS_MODULE: {count: lambda: []}})(lambda: count())
    assert calls_seen == before + 1


def test_magic_draws_from_the_innermost_model_call():
    nested = {THIS_MODULE: {
        take: lambda obj: [obj],
        pair: lambda a, b: itertools.cycle([take(a) * 100]),
    }}
    assert run_model([_model_take, _model_pair], nested)(lambda: pair(3, 4)) == (300, 300)


def test_input_model():
    original_input = input
    assert run_model(models, magics)(lambda: (input(), input("name? "))) == ("lol", "lol")
    assert run_model(models, magics_by_name)(lambda: input("name? ")) == "name? "
    assert input is original_input


def test_input_model_rejects_non_strings():
    not_text = {builtins: {input: lambda prompt='': [42]}}
    with pytest.raises(BrokenVariant, match=r"_model_input\('name\? '\) drew 42 at x = magic\(str\)"):
        run_model(models, not_text)(lambda: input("name? "))


def test_simple_patch_replaces_a_builtin():
    original_print = print
    assert run_model([simple])(lambda: print("hi")) == "lol"
    assert print is original_print


def test_simple_patch_replaces_module_functions_and_from_imports():
    always_one = {random.randint: lambda a, b: 1}
    assert run_model([always_one])(both_randints) == (1, 1)
    assert random.randint is ORIGINAL_RANDINT


def test_simple_patches_mix_with_models():
    mixed = models + [{take: lambda obj: "taken"}]
    assert run_model(mixed, magics)(lambda: (take(1), random.randint(1, 6))) == ("taken", 3)


def test_simple_patch_returning_false_is_not_a_broken_variant():
    assert run_model([{take: lambda obj: False}])(lambda: take(1)) is False


def test_simple_patch_only_takes_functions():
    with pytest.raises(MissedModelObject, match="only replace functions, got <class 'test_effecful.Dog'>"):
        run_model([{Dog: lambda: None}])(lambda: None)


def test_simple_patch_needs_a_callable_replacement():
    with pytest.raises(MissedModelObject, match="must be callable, got 'lol' of type str"):
        run_model([{print: "lol"}])(lambda: None)


def test_simple_patch_of_unreferenced_function():
    with pytest.raises(MissedModelObject, match="no loaded module references"):
        run_model([{Dog().speak: lambda: "meow"}])(lambda: None)


@model(THIS_MODULE)
class _model_module_with_Box:
    # the model of a module holding the model of a class in it, as the module holds the class
    @model(take)
    def model_take(obj):
        x = magic()
        return x

    @model(Box)
    class _model_Box_inside:
        @model(Box.__init__)
        def __init__(self):
            self.value = magic(int)


def test_class_model_nested_in_a_module_model():
    assert [kind for kind, _ in get_models([_model_module_with_Box])] == [Model.MODULE, Model.CLASS]
    both = {THIS_MODULE: {take: lambda obj: [obj * 2]}, Box: {Box.__init__: lambda self: [7]}}
    assert run_model([_model_module_with_Box], both)(lambda: (take(2), Box().value)) == (4, 7)
    assert (take(2), Box().value) == (2, 1)


def test_nested_class_model_on_its_own():
    inside = {Box: {Box.__init__: lambda self: [7]}}
    assert run_model([_model_module_with_Box._model_Box_inside], inside)(lambda: (take(2), Box().value)) == (2, 7)


class Refused(Exception):
    def __init__(self, reason, code):
        super().__init__(reason)
        self.code = code


def ask(question):
    return "yes"


@model(Refused)
class RefusedCopy(Exception):
    # a copy of Refused, which the model raises in its place
    def __init__(self, reason, code):
        Exception.__init__(self, reason)
        self.code = code


@model(THIS_MODULE)
class _model_ask:
    _model_Refused = RefusedCopy

    @model(ask)
    def model_ask(question):
        answer = magic(str)
        if answer == "no":
            raise RefusedCopy(f"{question} no", 403) from KeyError(question)
        return answer


ASKED_NO = {THIS_MODULE: {ask: lambda question: ["no"]}}


def test_exception_model_kind():
    assert RefusedCopy.__model_kind__ is Model.COPY
    assert [kind for kind, _ in get_models([_model_ask])] == [Model.MODULE, Model.COPY]


def test_exception_model_stands_in_for_the_original_in_the_run():
    def polite():
        try:
            return ask("tea?")
        except Refused as error:
            return type(error), error.code
    assert run_model([_model_ask], ASKED_NO)(polite) == (RefusedCopy, 403)
    assert Refused is not RefusedCopy


def test_exception_model_leaves_the_run_as_the_original():
    with pytest.raises(Refused) as caught:
        run_model([_model_ask], ASKED_NO)(lambda: ask("tea?"))
    assert type(caught.value) is Refused
    assert (caught.value.args, caught.value.code) == (("tea? no",), 403)
    assert isinstance(caught.value.__cause__, KeyError)
    assert caught.traceback[-1].name == "model_ask"


class Point:
    def __init__(self, x):
        self.x = x


class copies:
    # copies named like the classes they copy, kept apart from them
    @model(Point)
    class Point:
        def __init__(self, x):
            self.x = x


def test_copy_of_a_class_stands_in_for_it_in_the_run():
    assert copies.Point.__model_kind__ is Model.COPY
    made = run_model([copies.Point])(lambda: Point(1))
    assert isinstance(made, copies.Point) and made.x == 1
    assert run_model([copies.Point])(lambda: isinstance(copies.Point(2), Point))
    assert Point is not copies.Point and isinstance(Point(3), Point)


def test_original_exception_drawn_in_the_run_is_its_copy():
    drawn = {THIS_MODULE: {ask: lambda question: [Refused("built before", 500)]}}
    copy_kind = {}

    @model(THIS_MODULE)
    class _model_ask_for_an_error:
        _model_Refused = RefusedCopy

        @model(ask)
        def model_ask(question):
            error = magic(Exception)
            copy_kind["drawn"] = type(error), error.code
            raise error

    with pytest.raises(Refused, match="built before"):
        run_model([_model_ask_for_an_error], drawn)(lambda: ask("tea?"))
    assert copy_kind["drawn"] == (RefusedCopy, 500)


def test_other_exceptions_leave_the_run_as_they_are():
    with pytest.raises(BrokenVariant):
        run_model([_model_ask], {THIS_MODULE: {ask: lambda question: [1]}})(lambda: ask("tea?"))


def test_model_kinds():
    assert _model_Dog.__model_kind__ is Model.CLASS
    assert _model_Random.__model_kind__ is Model.MODULE
    assert _model_Random.model_randomint.__model_kind__ is Model.FUNCTION
    assert [kind for kind, _ in get_models([_model_Dog, _model_Random, model_pair_alone, simple, Dog])] == [
        Model.CLASS, Model.MODULE, Model.FUNCTION, Model.PATCHES, Model.MODULE,
    ]


def test_function_model_on_its_own():
    by_position = {THIS_MODULE: {pair: lambda a, b: [a * 10, b * 10]}}
    assert run_model([model_pair_alone], by_position)(lambda: pair(1, 2)) == (10, 20)
    by_name = {THIS_MODULE: {pair: lambda a, b: {"x": [b], "y": [a]}}}
    assert run_model([model_pair_alone], by_name)(lambda: pair(1, 2)) == (2, 1)
    assert pair(1, 2) == (1, 2)


def test_model_returns_its_result():
    assert run_model(models, magics)(lambda: random.randint(1, 6)) == 3
    assert run_model(models, magics)(lambda: random.randint(10, 20)) == 15


def test_model_returns_several_magic_values():
    quotient_and_remainder = {THIS_MODULE: {divide: lambda a, b: [a // b, a % b]}}
    assert run_model([model_divide], quotient_and_remainder)(lambda: divide(7, 2)) == (3, 1)


def test_broken_constraint_shows_the_drawn_values():
    wrong_remainder = {THIS_MODULE: {divide: lambda a, b: [3, 0]}}
    with pytest.raises(BrokenVariant, match=r"model_divide\(7, 2\) drew \[3, 0\] and broke constraint\(q \* b \+ r"):
        run_model([model_divide], wrong_remainder)(lambda: divide(7, 2))


def test_model_can_return_false():
    missing = {THIS_MODULE: {exists: lambda path: [False]}}
    assert run_model([model_exists], missing)(lambda: exists("nope.txt")) is False


def test_model_without_magic_returns_its_result():
    assert run_model([model_ping])(lambda: ping()) == "pong"


def test_model_raises_for_a_valid_magic_value():
    def fetch():
        try:
            return request("https://example.com/page")
        except NotFound as error:
            return f"missing: {error}"

    found = {THIS_MODULE: {request: lambda url: [200]}}
    missing = {THIS_MODULE: {request: lambda url: [404]}}
    assert run_model([model_request], found)(fetch) == 200
    assert run_model([model_request], missing)(fetch) == "missing: https://example.com/page was not found"


def test_model_raises_the_error_it_describes():
    typed_a_number = {THIS_MODULE: {read_line: lambda: [23]}}
    with pytest.raises(TypeError, match="The value 23 is a int, but it should be a string"):
        run_model([model_read_line], typed_a_number)(lambda: read_line())
    assert run_model([model_read_line], {THIS_MODULE: {read_line: lambda: ["hi"]}})(lambda: read_line()) == "hi"


def test_magic_of_several_kinds():
    assert run_model([model_identifier], {THIS_MODULE: {identifier: lambda name: ["abc"]}})(
        lambda: identifier("x")) == "abc"
    assert run_model([model_identifier], {THIS_MODULE: {identifier: lambda name: [7]}})(lambda: identifier("x")) == 7
    with pytest.raises(BrokenVariant, match=r"drew 1.5 at y = magic\(str, int\) .*which is not str or int"):
        run_model([model_identifier], {THIS_MODULE: {identifier: lambda name: [1.5]}})(lambda: identifier("x"))


# ------------------------------------------------------------------ compiling

CHECKER_SOURCE = """\
# compiled from MagicF models for a model checker, which supplies magic()


def randint(min, max):
    x = magic(int)
    assert isinstance(x, int)
    assert min <= x <= max
    return x


def input(prompt=''):
    x = magic(str)
    assert isinstance(x, str)
    return x
"""

CONCRETE_SOURCE = """\
# compiled from MagicF models, with the real effects in place of magic()
import builtins
import random


def randint(min, max):
    try:
        x = random.randint(min, max)
    except Exception as _error:
        raise AssertionError(f'the real randint raised {_error!r}, which its model never raises') from _error
    assert isinstance(x, int)
    assert min <= x <= max
    return x


def input(prompt=''):
    try:
        x = builtins.input(prompt)
    except Exception as _error:
        raise AssertionError(f'the real input raised {_error!r}, which its model never raises') from _error
    assert isinstance(x, str)
    return x
"""


def real_call_block(target, call, name, allowed=None):
    # the try block concrete compilation writes around the real effect
    lines = ["    try:", f"        {target} = {call}"]
    if allowed:
        lines += [f"    except {allowed}:", "        raise"]
    lines += [
        "    except Exception as _error:",
        f"        raise AssertionError(f'the real {name} raised {{_error!r}}, which its model never raises') from _error",
    ]
    return "".join(line + "\n" for line in lines)


def unpack_block(names, name):
    # the try block concrete compilation writes to unpack the real result into the model's magic values
    return (
        f"    try:\n        ({names}) = _result\n    except (TypeError, ValueError):\n"
        f"        raise AssertionError(f\"the real {name} returned {{_result!r}}, which doesn't unpack into its "
        f"model's magic values ({names})\") from None\n"
    )


def test_compile_for_a_model_checker():
    assert compile_models(models, Compilation.MODEL_CHECKER) == CHECKER_SOURCE


def test_compile_concrete():
    assert compile_models(models, Compilation.CONCRETE) == CONCRETE_SOURCE


def test_model_checker_source_runs_with_a_supplied_magic():
    namespace = {"magic": lambda kind: 4}
    exec(compile_models(models, Compilation.MODEL_CHECKER), namespace)
    assert namespace["randint"](1, 6) == 4
    with pytest.raises(AssertionError):
        namespace["randint"](5, 6)


def test_concrete_source_runs_the_real_effect_and_asserts_the_model():
    namespace = {}
    exec(compile_models(models, Compilation.CONCRETE), namespace)
    assert all(1 <= namespace["randint"](1, 6) <= 6 for _ in range(50))
    with mock.patch.object(builtins, "input", lambda prompt="": "typed"):
        assert namespace["input"]("name? ") == "typed"
    with mock.patch.object(builtins, "input", lambda prompt="": 42):
        with pytest.raises(AssertionError):
            namespace["input"]("name? ")


def test_compile_several_magic_values():
    checker = compile_models([model_divide], Compilation.MODEL_CHECKER)
    assert (
        "    q = magic(int)\n    assert isinstance(q, int)\n    r = magic(int)\n    assert isinstance(r, int)\n"
        "    assert q * b + r == a and 0 <= r < b\n    return (q, r)\n"
    ) in checker
    concrete = compile_models([model_divide], Compilation.CONCRETE)
    assert (
        real_call_block("_result", "test_effecful.divide(a, b)", "divide") + unpack_block("q, r", "divide")
        + "    assert isinstance(q, int)\n    assert isinstance(r, int)\n"
        "    assert q * b + r == a and 0 <= r < b\n    return (q, r)\n"
    ) in concrete
    namespace = {}
    exec(concrete, namespace)
    assert namespace["divide"](7, 2) == (3, 1)


def test_concrete_compile_unpacks_returned_magic_values():
    concrete = compile_models([model_pair_alone], Compilation.CONCRETE)
    assert (
        "def pair(a, b):\n" + real_call_block("_result", "test_effecful.pair(a, b)", "pair")
        + unpack_block("x, y", "pair") + "    return (x, y)\n"
    ) in concrete
    namespace = {}
    exec(concrete, namespace)
    assert namespace["pair"](1, 2) == (1, 2)


def test_compile_model_without_magic():
    assert "def ping():\n    return 'pong'\n" in compile_models([model_ping], Compilation.MODEL_CHECKER)
    concrete = compile_models([model_ping], Compilation.CONCRETE)
    assert (
        "def ping():\n" + real_call_block("_result", "test_effecful.ping()", "ping")
        + "    assert 'pong' == _result\n    return _result\n"
    ) in concrete
    namespace = {}
    exec(concrete, namespace)
    assert namespace["ping"]() == "pong"


def test_concrete_compile_asserts_a_computed_result():
    @model(inc)
    def model_inc_computed(a):
        return a + 1

    @model(inc)
    def model_inc_wrong(a):
        return a + 2

    concrete = compile_models([model_inc_computed], Compilation.CONCRETE)
    assert (
        real_call_block("_result", "test_effecful.inc(a)", "inc") + "    assert a + 1 == _result\n    return _result\n"
    ) in concrete
    namespace = {}
    exec(concrete, namespace)
    assert namespace["inc"](4) == 5

    namespace = {}
    exec(compile_models([model_inc_wrong], Compilation.CONCRETE), namespace)
    with pytest.raises(AssertionError):
        namespace["inc"](4)


def test_compile_imports_the_module_level_names_a_model_uses():
    checker = compile_models([model_cap], Compilation.MODEL_CHECKER)
    assert "from test_effecful import LIMIT\n" in checker
    assert "def cap(value):\n    x = magic(int)\n    assert isinstance(x, int)\n    assert x <= LIMIT\n    return x\n" in checker


def test_compile_magic_in_a_loop():
    checker = compile_models([model_dice], Compilation.MODEL_CHECKER)
    assert (
        "    for _ in range(n):\n        x = magic(int)\n        assert isinstance(x, int)\n        assert 1 <= x <= 6\n"
        "        rolls.append(x)\n"
        "    return rolls\n"
    ) in checker
    draws = itertools.count(1)
    namespace = {"magic": lambda kind: next(draws)}
    exec(checker, namespace)
    assert namespace["dice"](3) == [1, 2, 3]
    assert namespace["dice"](0) == []
    with pytest.raises(AssertionError):
        namespace["dice"](4)

    concrete = compile_models([model_dice], Compilation.CONCRETE)
    assert (
        real_call_block("_result", "test_effecful.dice(n)", "dice")
        + "    _magic = _elements(_result, 'dice')\n    rolls = []\n    for _ in range(n):\n"
        "        x = next(_magic)\n        assert isinstance(x, int)\n        assert 1 <= x <= 6\n"
        "        rolls.append(x)\n    assert rolls == _result\n    return _result\n"
    ) in concrete
    namespace = {}
    exec(concrete, namespace)
    rolled = namespace["dice"](5)
    assert len(rolled) == 5 and all(1 <= value <= 6 for value in rolled)


def test_compiled_loop_draws_like_the_running_model():
    checker = compile_models([_model_roll], Compilation.MODEL_CHECKER)
    draws = iter([10, 20, 30, 40, 50])
    namespace = {"magic": lambda kind: next(draws)}
    exec(checker, namespace)
    by_position = {THIS_MODULE: {roll: lambda n: iter([10, 20, 30, 40, 50])}}
    assert namespace["roll"](3) == run_model([_model_roll], by_position)(lambda: roll(3)) == [10, 20, 30, 40, 50]


def test_compile_magic_in_expressions():
    checker = compile_models([_model_inc], Compilation.MODEL_CHECKER)
    assert "    x = magic() + 1\n    values = [magic()]\n    y = z = magic()\n" in checker
    namespace = {"magic": iter([10, 20, 30]).__next__}
    exec(checker, namespace)
    assert namespace["inc"](10) == [11, 20, 30, 30]


def test_compile_keeps_the_exceptions_a_model_raises():
    checker = compile_models([model_request], Compilation.MODEL_CHECKER)
    assert (
        "    status_code = magic(int)\n    assert isinstance(status_code, int)\n    assert 100 <= status_code <= 599\n"
        "    if status_code == 404:\n"
        "        raise NotFound(f'{url} was not found')\n    return status_code\n"
    ) in checker
    assert "from test_effecful import NotFound\n" in checker


def test_concrete_compile_checks_exceptions_against_the_model():
    concrete = compile_models([model_request], Compilation.CONCRETE)
    assert real_call_block("status_code", "test_effecful.request(url)", "request", allowed="NotFound") in concrete
    namespace = {}
    exec(concrete, namespace)
    compiled_request = namespace["request"]

    assert compiled_request("https://example.com") == 200

    with mock.patch.object(THIS_MODULE, "request", lambda url: 404):
        with pytest.raises(AssertionError, match=r"the real request returned 404, but its model raises NotFound\("):
            compiled_request("https://example.com")

    def not_found(url):
        raise NotFound(url)

    with mock.patch.object(THIS_MODULE, "request", not_found):
        with pytest.raises(NotFound):
            compiled_request("https://example.com")

    def broken(url):
        raise KeyError("boom")

    with mock.patch.object(THIS_MODULE, "request", broken):
        with pytest.raises(AssertionError, match=r"the real request raised KeyError\('boom'\), which its model never raises"):
            compiled_request("https://example.com")


def test_concrete_compile_runs_the_real_effect_before_the_model_raises():
    namespace = {}
    exec(compile_models([model_randint_with_empty_range], Compilation.CONCRETE), namespace)
    assert 1 <= namespace["randint"](1, 6) <= 6
    with pytest.raises(ValueError, match="empty range"):
        namespace["randint"](6, 1)

    namespace = {}
    exec(compile_models(models, Compilation.CONCRETE), namespace)
    with pytest.raises(AssertionError, match=r"the real randint raised ValueError\(.*which its model never raises"):
        namespace["randint"](6, 1)


def test_compile_magic_of_several_kinds():
    checker = compile_models([model_identifier], Compilation.MODEL_CHECKER)
    assert "    y = magic(str, int)\n    assert isinstance(y, (str, int))\n    return y\n" in checker
    concrete = compile_models([model_identifier], Compilation.CONCRETE)
    assert (
        real_call_block("y", "test_effecful.identifier(name)", "identifier")
        + "    assert isinstance(y, (str, int))\n    return y\n"
    ) in concrete


def test_concrete_compile_needs_typed_magic_in_a_variable():
    @model(inc)
    def model_inc_typed_expression(a):
        x = magic(int) + 1
        return x

    with pytest.raises(UncompilableModel, match=r"uses magic\(int\) \(line \d+\) inside an expression"):
        compile_models([model_inc_typed_expression], Compilation.CONCRETE)


def test_compile_needs_its_iterator_names_free():
    @model(roll)
    def model_roll_into_result(n):
        _result = [magic() for _ in range(n)]
        return _result

    with pytest.raises(UncompilableModel, match=r"uses \['_result'\], which the compiled code needs"):
        compile_models([model_roll_into_result], Compilation.CONCRETE)


def test_compile_rejects_what_it_cannot_write_out():
    limit = 10

    @model(take)
    def model_take_limited(obj):
        x = magic()
        constraint(x < limit)
        return x

    @model(take)
    def model_take_constraint_in_expression(obj):
        x = magic()
        return constraint(x) or x

    @model(count)
    def model_count_without_return():
        constraint(True)

    with pytest.raises(UncompilableModel, match=r"uses \['limit'\] from the function around it"):
        compile_models([model_take_limited], Compilation.MODEL_CHECKER)
    with pytest.raises(UncompilableModel, match=r"inside an expression; write constraint\(\) as its own statement"):
        compile_models([model_take_constraint_in_expression], Compilation.MODEL_CHECKER)
    with pytest.raises(UncompilableModel, match="has no return to match against the result of the real count"):
        compile_models([model_count_without_return], Compilation.CONCRETE)
    with pytest.raises(UncompilableModel, match="compiling class models isn't supported yet"):
        compile_models([_model_Dog], Compilation.MODEL_CHECKER)
    with pytest.raises(UncompilableModel, match="plain patches"):
        compile_models([simple], Compilation.MODEL_CHECKER)
    with pytest.raises(UncompilableModel, match="both compile to a function named pair"):
        compile_models([_model_pair, model_pair_alone], Compilation.MODEL_CHECKER)


def test_concrete_compile_checks_a_reassigned_magic_variable():
    namespace = {}
    exec(compile_models([model_number_reassigned], Compilation.CONCRETE), namespace)
    with pytest.raises(AssertionError):
        namespace["number"]()


def test_concrete_compile_of_one_magic_in_a_branch():
    concrete = compile_models([model_number_in_branch], Compilation.CONCRETE)
    assert "    _magic = iter((_result,))\n" in concrete
    namespace = {}
    exec(concrete, namespace)
    assert namespace["number"]() == 5


def test_concrete_compile_reports_a_result_that_does_not_unpack():
    namespace = {}
    exec(compile_models([model_triple_as_pair], Compilation.CONCRETE), namespace)
    with pytest.raises(
        AssertionError, match=r"the real triple returned \(1, 2, 3\), which doesn't unpack into its model's magic values \(q, r\)"
    ):
        namespace["triple"]()


def test_concrete_compile_checks_falling_off_the_end():
    namespace = {}
    exec(compile_models([model_negative_falling_off], Compilation.CONCRETE), namespace)
    with pytest.raises(AssertionError, match="the real negative returned -1, but its model returns None for it"):
        namespace["negative"]()


def test_concrete_compile_reports_drawing_more_than_the_real_result_has():
    namespace = {}
    exec(compile_models([model_two_drawing_three], Compilation.CONCRETE), namespace)
    with pytest.raises(AssertionError, match=r"drew more values than the real two returned in \[1, 2\]"):
        namespace["two"]()


def test_concrete_compile_reports_a_result_without_elements():
    namespace = {}
    exec(compile_models([model_number_drawing_twice], Compilation.CONCRETE), namespace)
    with pytest.raises(AssertionError, match=r"the real number returned 5, which has no elements"):
        namespace["number"]()


def test_concrete_compile_lets_exceptions_through_when_a_raise_is_unreadable():
    concrete = compile_models([model_fetch_missing], Compilation.CONCRETE)
    assert "try:" not in concrete
    namespace = {}
    exec(concrete, namespace)
    with pytest.raises(KeyError):
        namespace["fetch_missing"]()


def test_compile_recognizes_magic_and_constraint_through_the_module():
    checker = compile_models([model_number_qualified], Compilation.MODEL_CHECKER)
    assert "def number():\n    x = magic(int)\n    assert isinstance(x, int)\n    assert x > 0\n    return x\n" in checker
    assert "import" not in checker
    namespace = {}
    exec(compile_models([model_number_qualified], Compilation.CONCRETE), namespace)
    assert namespace["number"]() == 5
    by_name = {THIS_MODULE: {number: lambda: {"x": [7]}}}
    assert run_model([model_number_qualified], by_name)(lambda: number()) == 7


def test_compile_imports_names_used_in_defaults_and_annotations():
    concrete = compile_models([model_write], Compilation.CONCRETE)
    assert "from test_effecful import DEFAULT_MODE\n" in concrete
    assert "from test_effecful import Path\n" in concrete
    namespace = {}
    exec(concrete, namespace)
    assert namespace["write"]("file.txt") == "r"


def test_concrete_compile_needs_the_original_defaults():
    with pytest.raises(
        UncompilableModel, match=r"model_write_other_default gives mode the default 'w', but the real write gives mode the default 'r'"
    ):
        compile_models([model_write_other_default], Compilation.CONCRETE)
    assert "def write(path, mode='w'):" in compile_models([model_write_other_default], Compilation.MODEL_CHECKER)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
