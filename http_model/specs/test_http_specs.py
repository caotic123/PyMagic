# the specs of http_model.specs, run on the httplib2 model, and compiled with it to run in pure Python

import ast

import pytest

from PyMagic import BrokenVariant, Compilation, RunningUnstatedModel, always, compile_models, need, run_spec, spec
from http_model import models
from http_model.magics import module_magics, reply
from http_model.specs import (
    REDIRECTS, a_cookie_or_a_consolation, a_redirect_is_followed, a_redirect_needs_its_location,
    a_stale_connection_is_retried_once, an_unreachable_network_is_tried_twice, compressed_content_comes_back,
    fortune, head_has_no_body, headers_come_back_by_lowercase_name, magics, magics_with, network_errors_are_what_they_are,
    specs, the_fortune_comes_back,
)

run = run_spec(models, magics)

TEXTS = ["", "hello", "naïve café", "日本語", "🍪", "a/b?c#d&e=f", "  spaces  ", "new\nline", "100%", '"quoted"']


def counts(checked):
    return len(checked.held), len(checked.unmet)


# ------------------------------------------------------------------ the specs hold

def test_a_cookie_or_a_consolation_for_every_status():
    # 0 to 100, 1000 and "x" aren't statuses; nor are redirects, 300 and 304 consolations
    assert counts(run(a_cookie_or_a_consolation.forall, status=[*range(0, 1001), "x"])) == (892, 110)


def test_redirects():
    assert counts(run(a_redirect_is_followed.forall, code=range(300, 309))) == (5, 4)
    assert counts(run(a_redirect_needs_its_location.forall, code=range(300, 309))) == (6, 3)


def test_what_the_server_sends_comes_back():
    assert counts(run(the_fortune_comes_back.forall, text=TEXTS)) == (10, 0)
    checked = run(compressed_content_comes_back.forall, encoding=["gzip", "deflate", "br"], text=[*TEXTS, "x" * 5000])
    assert counts(checked) == (22, 11)
    assert counts(run(headers_come_back_by_lowercase_name.forall, name=["X-Cookie", "ETag", "content-type"],
                      value=["1", "a b", ""])) == (9, 0)


def test_head_has_no_body():
    assert counts(run(head_has_no_body.forall, status=range(200, 600))) == (394, 6)
    # a HEAD redirected by a 303 is followed with a GET, whose body is no counterexample
    answers = iter([reply(status=303, headers={"location": "/elsewhere"}), reply(b"a body", status=200)])
    redirecting = magics_with(lambda self, uri, *args, **kwargs: next(answers))
    assert run_spec(models, redirecting)(head_has_no_body, 503) is None


def test_network_errors():
    assert counts(run(an_unreachable_network_is_tried_twice.forall, failures=range(4))) == (4, 0)
    assert counts(run(a_stale_connection_is_retried_once.forall, failures=range(4))) == (4, 0)
    assert counts(run(network_errors_are_what_they_are.forall)) == (1, 0)


# ------------------------------------------------------------------ the values the user gives

@spec
def any_status_is_a_cookie_or_a_consolation(status):
    # broken: it needs no status the server can send
    need(isinstance(status, int) and status not in (*REDIRECTS, 300, 304))
    always(isinstance(fortune(f"/status/{status}"), str))


def test_a_status_no_server_sends_is_no_case():
    with pytest.raises(BrokenVariant, match=r"^checking any_status_is_a_cookie_or_a_consolation\(status=0\): "
                                            r".*Http.request\(.*'http://cookies.example/status/0', 'GET'\) drew "
                                            r"\[None, 0\] and broke constraint\(101 <= status <= 999\)"):
        run(any_status_is_a_cookie_or_a_consolation.forall, status=range(0, 1001))


def answering(*draws):
    # the magics of a server that answers every request with these draws, by position
    return magics_with(lambda self, uri, *args, **kwargs: list(draws))


@pytest.mark.parametrize("given, raised, message", [
    (magics_with(lambda self, uri, *args, **kwargs: {key: value for key, value in reply(status=404).items()
                                                     if key != "content"}),
     RunningUnstatedModel, r"assigns content = magic\(\) but its magic generator gave no values for 'content'"),
    (answering(None, 404, "Not Found", 11, {}),
     RunningUnstatedModel, r"ran out of magic values at content = magic\(bytes\)"),
    (magics_with(lambda self, uri, *args, **kwargs: reply(status=404, version=12)),
     BrokenVariant, r"drew \[None, 404, 'Not Found', 12\] and broke constraint\(version in \(10, 11\)\)"),
    (magics_with(lambda self, uri, *args, **kwargs: reply(status=404, headers={"x-n": 1})),
     BrokenVariant, r"drew \[None, 404, 'Not Found', 11, \{'x-n': 1\}\] and broke constraint\(all\("),
    ({key: value for key, value in magics.items() if key not in module_magics({})},
     RunningUnstatedModel, r"try_value_or_env\(<class 'int'>, None, 'httplib2_decode_limit_hard'\) reached "
                           r"variable = magic\(str, NoneType\) .*but no magics were given for it"),
])
def test_a_value_not_given_or_one_no_server_gives_names_the_spec_case(given, raised, message):
    with pytest.raises(raised, match=r"^checking a_cookie_or_a_consolation\(404\): .*" + message):
        run_spec(models, given)(a_cookie_or_a_consolation, 404)


# ------------------------------------------------------------------ compiled

def test_the_specs_compile_with_the_model_to_run_in_pure_python():
    files = compile_models([*models, *specs], Compilation.MODEL_CHECKER, pure=True)
    source = files["http_model/specs.py"]
    assert "def head_has_no_body(status):" in source
    assert not any(isinstance(node, ast.Assert) for node in ast.walk(ast.parse(source)))
    assert "#   http_model.specs.head_has_no_body -> .http_model.specs.head_has_no_body\n" in files["__init__.py"]


CHECKING = """
import builtins
values = []
builtins.magic = lambda *kinds: values.pop(0)
from compiled._pymagic import SpecPropertyInvalid
from compiled.http_model.specs import a_cookie_or_a_consolation, head_has_no_body

def check(spec, argument, draws):
    values[:] = [None] * 8 + draws  # Http() reads its four decode limits from the environment, two variables each
    try:
        spec(argument)
    except SpecPropertyInvalid as error:
        return str(error)
    return "held"

print(check(head_has_no_body, 503, [None, 503, "Service Unavailable", 11, {}]))
print(check(head_has_no_body, 503, [None, 200, "OK", 10, {"content-length": "3"}]))
print(check(head_has_no_body, 503, [None, 303, "See Other", 11, {"location": "/elsewhere"}, None, 200, "OK", 11, {}, b"a body"]))
print(check(a_cookie_or_a_consolation, 404, [None, 404, "Not Found", 11, {}, b"no such page"]))
print(check(a_cookie_or_a_consolation, 404, [None, 200, "OK", 11, {}, b'{"fortune": "You will write a spec today"}']))
"""


def test_a_compiled_spec_holds_for_any_answer_or_has_a_counterexample(run_package):
    # the checker gives any answer it likes: head_has_no_body holds for these, a redirect to a GET with a body among
    # them, while a_cookie_or_a_consolation states what the server of http_model.specs answers, and a 200 to
    # /status/404 is a counterexample to it
    files = compile_models([*models, head_has_no_body, a_cookie_or_a_consolation], Compilation.MODEL_CHECKER, pure=True)
    assert run_package(files, CHECKING, pure=True).splitlines() == [
        "held",
        "held",
        "held",
        "held",
        "the arguments broke always(told == FORTUNE if status == 200 else told.startswith(f'no cookie today ({status} ')): "
        "a counterexample to a_cookie_or_a_consolation",
    ]
