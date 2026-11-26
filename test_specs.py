import random

import pytest

import ast
import itertools

from PyMagic import (
    BrokenExistentialQuantifier, BrokenVariant, Checked, Compilation, ConditionalPropertyRaise, MissedModelObject,
    RunningUnstatedModel, SpecPropertyInvalid, UncompilableModel, always, compile_models, constraint, exists, magic,
    model, need, run_model, run_spec, spec,
)


@spec()
def max_property(x, y):
    need(isinstance(x, int) and isinstance(y, int))
    need(x and y)
    always(max(x, y) >= y)


@spec
def max_above(x, y):
    # broken: max(x, y) is y when y is the larger
    need(isinstance(x, int) and isinstance(y, int))
    always(max(x, y) > y)


def is_higher_than(n):
    return n > 100


@spec
def example_receive_existential(x):
    need(isinstance(x, exists))
    need(x.property is is_higher_than)
    always(is_higher_than(x.witness + 1))


@spec
def witness_succ_exists(x):
    need(isinstance(x, int))
    return exists(lambda y: y > x, x + 1)


@spec
def witness_pred_exists(x):
    # broken: x - 1 is no witness of a number above x
    need(isinstance(x, int))
    return exists(lambda y: y > x, x - 1)


@spec
def successor_of_anything(x):
    # broken: witness_succ_exists needs an int, which this doesn't
    always(witness_succ_exists(x).witness > x)


# ------------------------------------------------------------------ need and always

def test_a_spec_holding_returns_what_it_returns():
    assert max_property(3, 1) is None
    assert max_property(-4, 7) is None


def test_an_unmet_need_says_nothing_of_the_arguments():
    with pytest.raises(ConditionalPropertyRaise, match=r"max_property\('a', 1\) doesn't meet need\(isinstance\(x, int\) "
                                                       r"and isinstance\(y, int\)\) .*says nothing of these arguments"):
        max_property("a", 1)
    with pytest.raises(ConditionalPropertyRaise, match=r"max_property\(0, 3\) doesn't meet need\(x and y\)"):
        max_property(0, 3)


def test_a_broken_always_is_a_counterexample():
    with pytest.raises(SpecPropertyInvalid, match=r"max_above\(1, 2\) broke always\(max\(x, y\) > y\) "
                                                  r"\(.*test_specs.py:\d+\): a counterexample to max_above"):
        max_above(1, 2)


def test_need_and_always_outside_a_spec():
    need(1 < 2)
    always(1 < 2)
    with pytest.raises(ConditionalPropertyRaise, match=r"need\(1 > 2\) .* is unmet, outside any spec"):
        need(1 > 2)
    with pytest.raises(SpecPropertyInvalid, match=r"always\(1 > 2\) .* failed outside any spec"):
        always(1 > 2)


def test_spec_errors_get_through_except_exception():
    # like BrokenVariant, so the code a spec checks can't catch them
    for error in (ConditionalPropertyRaise, SpecPropertyInvalid, BrokenExistentialQuantifier):
        assert not issubclass(error, Exception)
    assert issubclass(BrokenExistentialQuantifier, SpecPropertyInvalid)


def test_spec_and_spec_called_are_the_same():
    assert spec() is spec
    assert max_property.__name__ == "max_property"
    with pytest.raises(TypeError, match=r"@spec can only decorate a function, got 5"):
        spec(5)


# ------------------------------------------------------------------ forall

def test_forall_checks_every_combination():
    checked = max_property.forall(x=[0, 1, -2, "a"], y=[0, 3])
    assert checked == Checked(held=[(1, 3), (-2, 3)], unmet=[
        (0, 0), (0, 3), (1, 0), (-2, 0), ("a", 0), ("a", 3),
    ])


def test_forall_takes_domains_by_position_too():
    checked = max_property.forall(range(-3, 4), range(-3, 4))
    assert len(checked.held) == 36 and len(checked.unmet) == 13


def test_forall_raises_the_first_counterexample():
    with pytest.raises(SpecPropertyInvalid, match=r"max_above\(x=2, y=2\) broke always\(max\(x, y\) > y\)"):
        max_above.forall(x=[2], y=[1, 2, 3])


def test_forall_needs_a_domain_for_each_argument():
    with pytest.raises(TypeError, match=r"forall\(\) of max_property\(x, y\) takes one domain for each argument: "
                                        r"missing a required argument: 'y'"):
        max_property.forall(x=[1])
    with pytest.raises(TypeError, match=r"unexpected keyword argument 'z'"):
        max_property.forall(x=[1], y=[1], z=[1])


def test_forall_of_an_empty_domain_checks_nothing():
    assert max_property.forall(x=[], y=[1]) == Checked()


# ------------------------------------------------------------------ exists

def test_exists_keeps_its_witness_and_property():
    higher = exists(is_higher_than, 101)
    assert higher.property is is_higher_than
    assert higher.witness == 101
    assert higher.prop is True
    assert repr(higher) == "exists(is_higher_than, 101)"


def test_exists_without_a_witness_is_broken():
    with pytest.raises(BrokenExistentialQuantifier, match=r"made exists\(is_higher_than, 5\) at .*, but "
                                                          r"is_higher_than\(5\) is False"):
        exists(is_higher_than, 5)


def test_prop_is_the_proof_the_property_gives():
    # there is an x above 100 with an int above it: the property's proof is another exists
    nested = exists(lambda x: exists(lambda y: y > x, x + 1), 101)
    assert nested.prop.witness == 102


def test_a_spec_receives_an_existential():
    checked = example_receive_existential.forall(x=[
        exists(is_higher_than, 101), exists(is_higher_than, 5000), exists(lambda n: n < 0, -1), 42,
    ])
    assert [x.witness for (x,) in checked.held] == [101, 5000]
    assert checked.unmet[0][0].witness == -1 and checked.unmet[1] == (42,)


def test_a_spec_makes_an_existential():
    succ = witness_succ_exists(41)
    assert succ.witness == 42 and succ.prop is True
    checked = witness_succ_exists.forall(x=[-1, 0, 41, "a"])
    assert checked.held == [(-1,), (0,), (41,)] and checked.unmet == [("a",)]


def test_a_spec_making_a_wrong_witness_is_broken():
    with pytest.raises(BrokenExistentialQuantifier, match=r"witness_pred_exists\(x=3\) made "
                                                          r"exists\(witness_pred_exists.<locals>.<lambda>, 2\) at "
                                                          r"return exists\(lambda y: y > x, x - 1\)"):
        witness_pred_exists.forall(x=[3])


def test_an_existential_from_one_spec_goes_to_another():
    higher = exists(is_higher_than, witness_succ_exists(100).witness)
    assert example_receive_existential(higher) is None


def test_a_spec_calling_another_outside_its_needs_is_broken():
    assert successor_of_anything.forall(x=[1, 2]).held == [(1,), (2,)]
    with pytest.raises(SpecPropertyInvalid, match=r"successor_of_anything\(x='a'\) relies on what another spec says "
                                                  r"nothing of: witness_succ_exists\('a'\) doesn't meet need"):
        successor_of_anything.forall(x=[1, "a"])


# ------------------------------------------------------------------ specs over models

def play():
    first, second = random.randint(1, 6), random.randint(1, 6)
    if first == second == 1:
        return "snake eyes: you lose everything"
    if first == second:
        return "doubles: you win a pizza"
    return f"{first + second}: roll again"


@model(random.randint)
def randint(a, b):
    face = magic(int)
    constraint(a <= face <= b)
    return face


def throw(*faces):
    landing = iter(faces)
    return run_model([randint], {random: {random.randint: lambda a, b: landing}})(play)


@spec
def snake_eyes_only_on_ones(first, second):
    need(1 <= first <= 6 and 1 <= second <= 6)
    always(throw(first, second).startswith("snake eyes") == (first == second == 1))


@spec
def any_faces_play(first, second):
    always(throw(first, second).endswith(("everything", "pizza", "again")))


def test_a_spec_of_a_program_under_its_models():
    checked = snake_eyes_only_on_ones.forall(first=range(8), second=range(8))
    assert len(checked.held) == 36 and len(checked.unmet) == 28


def test_a_spec_over_faces_no_die_shows_breaks_the_model():
    # what the model rejects is no case of the spec, so the spec must need it away
    with pytest.raises(BrokenVariant, match=r"^checking any_faces_play\(first=7, second=3\): randint\(1, 6\) drew \[7\]"):
        any_faces_play.forall(first=[1, 7], second=[3])


# ------------------------------------------------------------------ specs run under their models

def roll(n):
    return [random.randint(1, 6) for _ in range(n)]


def careful_roll(n):
    # a program that goes on without the dice when rolling them fails
    try:
        return roll(n)
    except Exception:
        return []


@spec
def rolls_show_faces(n):
    need(isinstance(n, int) and n >= 0)
    rolls = roll(n)
    always(len(rolls) == n and all(1 <= face <= 6 for face in rolls))


@spec
def careful_rolls_show_faces(n):
    need(isinstance(n, int) and n >= 0)
    always(len(careful_roll(n)) == n)


@spec
def two_rolls_show_faces(n):
    rolls_show_faces(n)
    rolls_show_faces(n)


@spec
def a_face_is_a_face():
    # calls the model itself, which no run_model gives values
    always(1 <= randint(1, 6) <= 6)


def dice(*faces):
    # the dice land on these faces, one throw after the other, whichever spec case throws them
    landing = iter(faces)
    return {random: {random.randint: lambda a, b: landing}}


def test_a_spec_runs_under_its_models_with_the_values_given():
    assert run_spec([randint], dice(3, 4))(rolls_show_faces, 2) is None
    assert run_spec([randint], dice(1, 1))(a_throw_ends_the_game) is None
    # the values given make a counterexample
    with pytest.raises(SpecPropertyInvalid, match=r"no_one_loses_everything\(\) broke always"):
        run_spec([randint], dice(1, 1))(no_one_loses_everything)
    # a spec can be in the models list, which puts nothing in place for it
    assert run_spec([randint, rolls_show_faces], dice(5))(rolls_show_faces, 1) is None


def test_forall_under_models_draws_the_values_case_after_case():
    # 0 + 1 + 2 faces; the case a need() leaves out reaches no model, and draws none
    checked = run_spec([randint], dice(3, 4, 5))(rolls_show_faces.forall, n=[-1, 0, 1, 2])
    assert checked == Checked(held=[(0,), (1,), (2,)], unmet=[(-1,)])
    # by name, the values of face
    sixes = {random: {random.randint: lambda a, b: {"face": itertools.cycle([6])}}}
    assert run_spec([randint], sixes)(rolls_show_faces.forall, n=range(4)).held == [(0,), (1,), (2,), (3,)]


def test_a_value_not_given_is_an_error_naming_the_spec_case():
    # three faces wanted, two given: the case of n=2 runs out
    with pytest.raises(RunningUnstatedModel, match=r"^checking rolls_show_faces\(n=2\): randint\(1, 6\) ran out of "
                                                   r"magic values at face = magic\(int\) .*its magic generator stopped"):
        run_spec([randint], dice(3, 4))(rolls_show_faces.forall, n=[1, 2])
    # no magics at all
    with pytest.raises(RunningUnstatedModel, match=r"^checking rolls_show_faces\(1\): randint\(1, 6\) reached "
                                                   r"face = magic\(int\) .*but no magics were given for it"):
        run_spec([randint])(rolls_show_faces, 1)
    # by name: no values left for face, or values for a variable the model doesn't have
    with pytest.raises(RunningUnstatedModel, match=r"^checking rolls_show_faces\(1\): randint\(1, 6\) ran out"):
        run_spec([randint], {random: {random.randint: lambda a, b: {"face": []}}})(rolls_show_faces, 1)
    with pytest.raises(RunningUnstatedModel, match=r"^checking rolls_show_faces\(1\): the magic generator for "
                                                   r"randint\(1, 6\) gave values for \['other'\], but randint only "
                                                   r"assigns magic\(\) to \['face'\]"):
        run_spec([randint], {random: {random.randint: lambda a, b: {"other": [1]}}})(rolls_show_faces, 1)


def test_a_model_called_outside_run_model_has_no_values():
    with pytest.raises(RunningUnstatedModel, match=r"^checking a_face_is_a_face\(\): randint\(1, 6\) reached "
                                                   r"face = magic\(int\) .*but no magics were given for it"):
        a_face_is_a_face.forall()


def test_a_value_not_given_is_neither_unmet_nor_a_counterexample():
    # forall() stops at it rather than counting the case, and the program's own except Exception can't take it
    with pytest.raises(RunningUnstatedModel, match=r"^checking careful_rolls_show_faces\(n=2\): randint\(1, 6\) ran out"):
        run_spec([randint], dice(3, 4))(careful_rolls_show_faces.forall, n=[1, 2])
    for error in (ConditionalPropertyRaise, SpecPropertyInvalid, Exception):
        assert not issubclass(RunningUnstatedModel, error)


def test_a_value_its_original_couldnt_give_is_an_error_naming_the_spec_case():
    with pytest.raises(BrokenVariant, match=r"^checking rolls_show_faces\(1\): randint\(1, 6\) drew \[9\] and broke "
                                            r"constraint\(a <= face <= b\)"):
        run_spec([randint], dice(9))(rolls_show_faces, 1)


def test_the_innermost_spec_names_the_case():
    # two rolls of two faces, three faces given: the second roll runs out
    with pytest.raises(RunningUnstatedModel) as unstated:
        run_spec([randint], dice(1, 2, 3))(two_rolls_show_faces.forall, n=[2])
    assert str(unstated.value).startswith("checking rolls_show_faces(2): randint(1, 6) ran out of magic values")
    assert str(unstated.value).count("checking") == 1


def test_a_spec_runs_with_run_spec_and_a_program_with_run_model():
    for spec_target in (rolls_show_faces, rolls_show_faces.forall):
        with pytest.raises(MissedModelObject, match=r"^rolls_show_faces is a spec; run it with run_spec\(models, "
                                                    r"magics\), not run_model"):
            run_model([randint], dice(3))(spec_target, 1)
    with pytest.raises(MissedModelObject, match=r"^run_spec runs a spec or its forall\(\), got roll; run it with "
                                                r"run_model\(models, magics\)"):
        run_spec([randint], dice(3))(roll, 1)
    # a program that calls a spec is a program
    assert run_model([randint], dice(3))(lambda: rolls_show_faces(1)) is None


def test_values_for_no_model_are_an_error():
    with pytest.raises(MissedModelObject, match=r"magics given for \['random.randint'\], but no model in \[\] models them"):
        run_spec([], dice(1))(rolls_show_faces, 1)


@spec
def a_throw_ends_the_game():
    always(play().endswith(("everything", "pizza", "again")))


@spec
def no_one_loses_everything():
    # broken: snake eyes do
    always(not play().startswith("snake eyes"))


def test_a_spec_is_in_the_models_list_for_nothing():
    landing = iter([2, 5])
    assert run_model([randint, a_throw_ends_the_game], {random: {random.randint: lambda a, b: landing}})(play) == \
        "7: roll again"


# ------------------------------------------------------------------ compiled

@spec
def needs_a_big_successor(x):
    need(witness_succ_exists(x).witness > 5)
    always(x >= 5)


SPECS = [max_property, max_above, example_receive_existential, witness_pred_exists, successor_of_anything,
         needs_a_big_successor]

COMPILED_MAX_PROPERTY = """\
def max_property(x, y):
    if not (isinstance(x, int) and isinstance(y, int)):
        raise ConditionalPropertyRaise("the arguments don't meet need(isinstance(x, int) and isinstance(y, int)): max_property says nothing of them")
    if not (x and y):
        raise ConditionalPropertyRaise("the arguments don't meet need(x and y): max_property says nothing of them")
    try:
        if not max(x, y) >= y:
            raise SpecPropertyInvalid('the arguments broke always(max(x, y) >= y): a counterexample to max_property')
    except ConditionalPropertyRaise as _unmet:
        raise SpecPropertyInvalid(f'max_property relies on what another spec says nothing of: {_unmet}') from _unmet
"""

COMPILED_NEEDS_A_BIG_SUCCESSOR = """\
def needs_a_big_successor(x):
    try:
        _met = witness_succ_exists(x).witness > 5
    except ConditionalPropertyRaise as _unmet:
        raise SpecPropertyInvalid(f'needs_a_big_successor relies on what another spec says nothing of: {_unmet}') from _unmet
    if not _met:
        raise ConditionalPropertyRaise("the arguments don't meet need(witness_succ_exists(x).witness > 5): needs_a_big_successor says nothing of them")
    if not x >= 5:
        raise SpecPropertyInvalid('the arguments broke always(x >= 5): a counterexample to needs_a_big_successor')
"""


def test_a_spec_compiles_to_raises_alone():
    files = compile_models(SPECS, Compilation.MODEL_CHECKER, pure=True)
    source = files["test_specs.py"]
    assert COMPILED_MAX_PROPERTY in source and COMPILED_NEEDS_A_BIG_SUCCESSOR in source
    tree = ast.parse(source)
    assert not any(isinstance(node, ast.Assert) for node in ast.walk(tree))
    assert not any(isinstance(node, ast.Call) and getattr(node.func, "id", None) in ("need", "always")
                   for node in ast.walk(tree))
    assert all(not line.startswith(("import ", "from ")) or line.startswith("from .") for line in source.splitlines())
    # the specs a compiled one calls are compiled with it, and so is what they use
    assert "\ndef witness_succ_exists(x):\n" in source and "\ndef is_higher_than(n):\n" in source
    assert "class exists:" in files["_pymagic.py"] and "class BrokenExistentialQuantifier(SpecPropertyInvalid):" in files["_pymagic.py"]


def test_a_pure_package_names_its_specs_in_comments():
    init = compile_models([max_property, successor_of_anything], Compilation.MODEL_CHECKER, pure=True)["__init__.py"]
    assert ast.parse(init).body == []
    assert init.endswith(
        "#   test_specs.max_property -> .test_specs.max_property\n"
        "#   test_specs.successor_of_anything -> .test_specs.successor_of_anything\n"
        "#   test_specs.witness_succ_exists -> .test_specs.witness_succ_exists\n"
    )


def test_a_package_has_a_table_of_its_specs():
    init = compile_models([max_property, randint], Compilation.MODEL_CHECKER)["__init__.py"]
    tables = {statement.targets[0].id: ast.literal_eval(statement.value) for statement in ast.parse(init).body}
    assert tables["SPECS"] == [("test_specs", "max_property", ".test_specs", "max_property")]
    assert ("random", "randint", ".random", "randint") in tables["REPLACED"]
    # without specs, there's no table of them
    assert "SPECS" not in compile_models([randint], Compilation.MODEL_CHECKER)["__init__.py"]


CHECKING = """
from compiled._pymagic import ConditionalPropertyRaise, SpecPropertyInvalid, exists
from compiled.test_specs import (
    example_receive_existential, is_higher_than, max_above, max_property, needs_a_big_successor, successor_of_anything,
    witness_pred_exists, witness_succ_exists,
)

def outcome(check):
    try:
        check()
    except ConditionalPropertyRaise as error:
        return f"unmet: {error}"
    except SpecPropertyInvalid as error:
        return f"{type(error).__name__}: {error}"
    return "held"

for check in [
    lambda: max_property(3, 1), lambda: max_property(0, 3), lambda: max_property("a", 1), lambda: max_above(1, 2),
    lambda: example_receive_existential(exists(is_higher_than, 101)), lambda: example_receive_existential(42),
    lambda: exists(is_higher_than, 5), lambda: witness_pred_exists(3),
    lambda: successor_of_anything(1), lambda: successor_of_anything("a"),
    lambda: needs_a_big_successor(5), lambda: needs_a_big_successor(3), lambda: needs_a_big_successor("a"),
]:
    print(outcome(check))
print(witness_succ_exists(41).witness)
"""


def test_compiled_specs_run_in_pure_python(run_package):
    files = compile_models(SPECS, Compilation.MODEL_CHECKER, pure=True)
    assert run_package(files, CHECKING, pure=True).splitlines() == [
        "held",
        "unmet: the arguments don't meet need(x and y): max_property says nothing of them",
        "unmet: the arguments don't meet need(isinstance(x, int) and isinstance(y, int)): max_property says nothing of them",
        "SpecPropertyInvalid: the arguments broke always(max(x, y) > y): a counterexample to max_above",
        "held",
        "unmet: the arguments don't meet need(isinstance(x, exists)): example_receive_existential says nothing of them",
        "BrokenExistentialQuantifier: made exists(is_higher_than, 5), but the property gives False for the witness: "
        "the witness doesn't have the property",
        "BrokenExistentialQuantifier: made exists(witness_pred_exists.<locals>.<lambda>, 2), but the property gives "
        "False for the witness: the witness doesn't have the property",
        "held",
        "SpecPropertyInvalid: successor_of_anything relies on what another spec says nothing of: the arguments don't "
        "meet need(isinstance(x, int)): witness_succ_exists says nothing of them",
        "held",
        "unmet: the arguments don't meet need(witness_succ_exists(x).witness > 5): needs_a_big_successor says nothing "
        "of them",
        "SpecPropertyInvalid: needs_a_big_successor relies on what another spec says nothing of: the arguments don't "
        "meet need(isinstance(x, int)): witness_succ_exists says nothing of them",
        "42",
    ]


def test_the_runtime_agrees_with_the_compiled_specs():
    assert needs_a_big_successor.forall(x=[5, 3]) == Checked(held=[(5,)], unmet=[(3,)])
    with pytest.raises(SpecPropertyInvalid, match=r"needs_a_big_successor\(x='a'\) relies on what another spec"):
        needs_a_big_successor.forall(x=["a"])


def test_a_compiled_spec_checks_a_program_under_its_compiled_models(run_package):
    files = compile_models([randint, a_throw_ends_the_game, no_one_loses_everything], Compilation.MODEL_CHECKER, pure=True)
    script = (
        "import builtins\nfaces = iter([1, 1, 2, 5, 1, 1, 7, 3])\nbuiltins.magic = lambda *kinds: next(faces)\n"
        "from compiled._pymagic import BrokenVariant, SpecPropertyInvalid\n"
        "from compiled.test_specs import a_throw_ends_the_game, no_one_loses_everything\n"
        "a_throw_ends_the_game()\na_throw_ends_the_game()\n"
        "try:\n    no_one_loses_everything()\nexcept SpecPropertyInvalid as error:\n    print(error)\n"
        "try:\n    a_throw_ends_the_game()\nexcept BrokenVariant as error:\n    print(error)\n"
    )
    # no die shows a 7: the compiled model raises its BrokenVariant, which is no counterexample to the spec
    assert run_package(files, script, pure=True).splitlines() == [
        "the arguments broke always(not play().startswith('snake eyes')): a counterexample to no_one_loses_everything",
        "the values broke a <= face <= b: the model of randint rejected this call",
    ]


@spec
def need_in_a_branch(x):
    if x:
        need(x > 0)


@spec
def need_in_an_expression(x):
    return need(x)


def positive(x):
    always(x > 0)


@spec
def always_in_a_helper(x):
    positive(x)


@spec
def names_unmet(x):
    _unmet = x
    always(_unmet)


@spec
def constrains(x):
    constraint(x > 0)
    always(x)


@spec
def checks_another_forall(xs):
    always(witness_succ_exists.forall(xs).held)


@spec
def all_have_successors(xs):
    # reaches witness_succ_exists through builtins, which call it for each of xs
    always(all(map(witness_succ_exists, xs)))


def test_a_spec_reached_through_a_builtin_is_relied_on_compiled_too(run_package):
    with pytest.raises(SpecPropertyInvalid, match=r"all_have_successors\(\[1, 'a'\]\) relies on what another spec"):
        all_have_successors([1, "a"])
    files = compile_models([all_have_successors], Compilation.MODEL_CHECKER, pure=True)
    script = (
        "from compiled._pymagic import SpecPropertyInvalid\nfrom compiled.test_specs import all_have_successors\n"
        "all_have_successors([1, 2])\n"
        "try:\n    all_have_successors([1, 'a'])\nexcept SpecPropertyInvalid as error:\n    print(error)\n"
    )
    assert run_package(files, script, pure=True).splitlines() == [
        "all_have_successors relies on what another spec says nothing of: the arguments don't meet "
        "need(isinstance(x, int)): witness_succ_exists says nothing of them",
    ]


def test_specs_of_two_modules_sharing_a_helper_dont_import_each_other(tmp_path, monkeypatch, run_package):
    (tmp_path / "spec_b.py").write_text(
        "from PyMagic import always, need, spec\n\n"
        "def helper(x):\n    return x + 1\n\n"
        "@spec\ndef inner(x):\n    need(isinstance(x, int))\n    always(helper(x) > x)\n"
    )
    (tmp_path / "spec_a.py").write_text(
        "from PyMagic import always, spec\nfrom spec_b import helper, inner\n\n"
        "@spec\ndef outer(x):\n    always(helper(x) > x)\n    inner(x)\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    from spec_a import outer
    files = compile_models([outer], Compilation.MODEL_CHECKER, pure=True)
    # the helper is compiled into the module named after its own, with the spec that is defined beside it
    assert "def helper(x):" in files["spec_b.py"] and "def helper(x):" not in files["spec_a.py"]
    assert "spec_a" not in files["spec_b.py"]
    script = "from compiled.spec_a import outer\nfrom compiled.spec_b import inner\nouter(1)\ninner(2)\nprint('held')"
    assert run_package(files, script, pure=True).split() == ["held"]


def test_what_a_spec_compiles_from():
    for broken, message in [
        (constrains, r"constrains calls constraint\(x > 0\) \(line \d+\), which is a model's"),
        (checks_another_forall, r"checks_another_forall uses witness_succ_exists.forall \(line \d+\), which a compiled "
                                r"spec doesn't have"),
        (a_face_is_a_face, r"a_face_is_a_face calls the model randint itself, which has no magic values to draw: call "
                           r"Random.randint"),
        (need_in_a_branch, r"need_in_a_branch calls need\(x > 0\) \(line \d+\) inside a block or a nested function; "
                           r"write need\(\) as a statement of the spec's own body"),
        (need_in_an_expression, r"need_in_an_expression uses need\(x\) \(line \d+\) inside an expression"),
        (always_in_a_helper, r"uses always\(\) outside a spec, where it doesn't compile"),
        (names_unmet, r"names_unmet uses \['_unmet'\], which the compiled code of a spec needs"),
    ]:
        with pytest.raises(UncompilableModel, match=message):
            compile_models([broken], Compilation.MODEL_CHECKER)
    with pytest.raises(UncompilableModel, match=r"max_property is a spec, which compiles for a model checker"):
        compile_models([max_property], Compilation.CONCRETE)
