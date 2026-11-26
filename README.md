# PyMagic

PyMagic is a library used to write specs/models in a natural way in Python. It can be used to write formal specs.
One clear problem when testing programs is effects: one would like to test if the program behaves correctly, but running against a real workflow is sometimes
complex/dangerous/work-heavy; the constraints are several...

Usually we do not need to run these effects; we just need to make sure the program simulates/respect the specs defined by these effects. State machines simulate http/database pipelines; generality simulates non-trivial properties as a hash function; for instance, one could just state that a hash function is not collision-free; see here that although hash functions are not always effectful computations, we may still model them instead of computing them. In fact, PyMagic is not designed to attack only effects; sometimes complex computations are better abstracted rather than run.

A program in PyMagic looks like this:

```python
@model(Example.HTTP.Request)
def request(url):
    (res, status_code) = magic()
    constraint(isinstance(res, str) and is_valid_json(res))
    constraint(isinstance(status_code, int))
    if status_code is 404:
        raise HTTP_error404
    ...
    res = parse(res)
    return res
```

See that by replacing magic with its proper side effect, we can effectively run the request.
Also, the code is runnable and should match the semantics if you try to run it by selecting valid values or using a symbolic engine; of course, you need to introduce semantics for magic. Given that a Python object is non-empty, one model is given a Python object x; we select one x such that the constraints that meet x are true. Running by selecting valid values is already given by the library. The decorator allows mocking values, and when testing using concrete values the constraints become assertions. This allows you to test if your model respects your specs.

*LLM disclaimer: This library was written partially with the help of LLMs, mostly the compilation part; the code was reviewed, although*
*Pr are very welcome, however not pure LLM-based. The code still has to be complete or at least partially understood by a human*
# LLM iteration

Just simply asking the LLM to find bugs using models/compiled models is enough to find bugs that would be hidden if no model is designed. Models allow them to reason about specs (Although vibe-coding models without review is not preferred over reviewed human models). The first step is just starting to refine a model. Mocking real values that mimic the effectful/magic computation is the second phase. In fact, this substantially increases the debugging surface, since it make clear to the LLM iteration that a proper spec should be written and respect. 


# How to use

PyMagic needs Python 3.10 or later. It's on PyPI as `pymagic-models`; the httplib2 model needs the `httplib2`
extra:

```bash
pip install pymagic-models               # PyMagic and the standard library models
pip install "pymagic-models[httplib2]"   # and the httplib2 model, with httplib2
```

You import it as `PyMagic`. From a clone of the repository, the tests run with `pytest`:

```bash
pip install -e ".[test]"
python3.10 -m pytest          # the tests of PyMagic and of the models
```

## Model an effect: a dice game

A game of dice: doubles win a pizza, snake eyes lose everything. To test it, the dice must land where we say,
so we model `random.randint`. Each face is a `magic()` value we choose, and the constraint says which faces a
die can show:

```python
import random
from collections import Counter

from PyMagic import BrokenVariant, constraint, magic, model, run_model


def play():
    # roll two dice: doubles win a pizza, snake eyes lose everything
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


def dice(*faces):
    # the dice land on these faces, one throw after the other
    landing = iter(faces)
    return {random: {random.randint: lambda a, b: landing}}


def throw(*faces):
    return run_model([randint], dice(*faces))(play)
```

`run_model` puts the model in place of `random.randint` while `play` runs. The magic generator receives each
call's arguments and returns the values that call's `magic()` draws:

```python
>>> throw(1, 1)
'snake eyes: you lose everything'
>>> throw(4, 4)
'doubles: you win a pizza'
>>> throw(2, 5)
'7: roll again'
>>> throw(7, 3)    # no die lands on 7
BrokenVariant: randint(1, 6) drew [7] and broke constraint(a <= face <= b) (dice.py:20): the model of Random.randint rejected this call
```

Values that break a constraint aren't something the real effect could do, so the model rejects them with a
`BrokenVariant` rather than letting the game run on a 7.

### Every variant at once

Because the dice are ours, we can throw every pair and look at the whole game, not a lucky sample:

```python
>>> Counter(throw(a, b).split(":")[0] for a in range(1, 7) for b in range(1, 7))
Counter({'7': 6, 'doubles': 5, '5': 4, '6': 4, '8': 4, '9': 4, '3': 2, '4': 2, '10': 2, '11': 2, 'snake eyes': 1})
```

The house gives away a pizza 5 times in 36.

### Values by name

A magic generator can also give values by the variable that `magic()` is assigned to, here `face`:

```python
>>> run_model([randint], {random: {random.randint: lambda a, b: {"face": [6]}}})(play)
'doubles: you win a pizza'
```

## Write a spec: what holds for every argument

A spec is a function that states a property of all its arguments. `need()` says which arguments it speaks of,
and `always()` says what holds of them. An argument that fails a `need()` raises `ConditionalPropertyRaise`: the
spec says nothing of it. One that meets every `need()` but fails an `always()` raises `SpecPropertyInvalid`: it is a
counterexample.

```python
from PyMagic import always, need, spec


@spec
def max_property(x, y):
    need(isinstance(x, int) and isinstance(y, int))
    need(x and y)
    always(max(x, y) >= y)
```

Called with arguments, a spec checks them. `forall()` checks every combination of the values given for each
argument, and gives back which ones held and which a `need()` left out. The first combination that fails an
`always()` raises its `SpecPropertyInvalid`:

```python
>>> max_property(3, 1)
>>> max_property(0, 3)
ConditionalPropertyRaise: max_property(0, 3) doesn't meet need(x and y) (max.py:7): max_property says nothing of these arguments
>>> max_property.forall(x=[0, 1, -2], y=[2, 3])
Checked(held=[(1, 2), (1, 3), (-2, 2), (-2, 3)], unmet=[(0, 2), (0, 3)])
```

A spec can check a program under its models. Here is one for the dice game, over the faces both dice land on:

```python
@spec
def snake_eyes_only_on_ones(first, second):
    need(1 <= first <= 6 and 1 <= second <= 6)
    always(throw(first, second).startswith("snake eyes") == (first == second == 1))
```

```python
>>> checked = snake_eyes_only_on_ones.forall(first=range(8), second=range(8))
>>> len(checked.held), len(checked.unmet)
(36, 28)
```

Without the `need()`, `throw(7, 3)` would raise the model's `BrokenVariant`. No die shows a 7, so that is no case
of the spec, and the spec must rule it out with a `need()`.

### Run a spec with its models

`run_spec(models, magics)` runs a spec, or its `forall()`, as `run_model` runs a program: the models it reaches
draw their `magic()` values from the magics, and the user gives every one. `run_model` refuses a spec, and
`run_spec` anything but one; a program that calls a spec is still a program, for `run_model`. With `forall()`, the cases draw them one after the other, and a case that a
`need()` leaves out draws none. A value that isn't given raises `RunningUnstatedModel`, and one the original
couldn't give raises `BrokenVariant`. Either way the error says which spec call wanted the value, and it is neither a
case left out nor a counterexample: `forall()` stops at it.

```python
def roll(n):
    return [random.randint(1, 6) for _ in range(n)]


@spec
def rolls_show_faces(n):
    need(isinstance(n, int) and n >= 0)
    rolls = roll(n)
    always(len(rolls) == n and all(1 <= face <= 6 for face in rolls))
```

```python
>>> run_spec([randint], dice(3, 4))(rolls_show_faces, 2)
>>> run_spec([randint], dice(3, 4, 5))(rolls_show_faces.forall, n=[-1, 0, 1, 2])
Checked(held=[(0,), (1,), (2,)], unmet=[(-1,)])
>>> run_spec([randint], dice(3, 4))(rolls_show_faces.forall, n=[1, 2])
RunningUnstatedModel: checking rolls_show_faces(n=2): randint(1, 6) ran out of magic values at face = magic(int) (dice.py:20); its magic generator stopped before giving one (use itertools.cycle([...]) to repeat values)
```

### There exists

`exists(property, witness)` shows that some value has a property by giving one that does. It keeps the
`property`, the `witness`, and `prop`, which is what the property gives for the witness. A witness without the
property raises `BrokenExistentialQuantifier`, which is a `SpecPropertyInvalid`, so a spec that makes one is broken.
One spec can make an existential, and another can take one:

```python
from PyMagic import exists


def is_higher_than(n):
    return n > 100


@spec
def witness_succ_exists(x):
    # every int has one above it: x + 1
    need(isinstance(x, int))
    return exists(lambda y: y > x, x + 1)


@spec
def example_receive_existential(x):
    need(isinstance(x, exists))
    need(x.property is is_higher_than)
    always(is_higher_than(x.witness + 1))
```

```python
>>> witness_succ_exists(41).witness
42
>>> example_receive_existential(exists(is_higher_than, 101))
>>> exists(is_higher_than, 5)
BrokenExistentialQuantifier: made exists(is_higher_than, 5) at exists(...) (<stdin>:1), but is_higher_than(5) is False: the witness doesn't have the property
```

A spec that calls another must meet the other's needs. If it passes arguments the other says nothing of, the
caller raises `SpecPropertyInvalid`: its own case is a counterexample, not one to leave out.

## Model a library: a fortune cookie service over httplib2

`http_model` models httplib2, and `stdlib_model` models the parts of the standard library it uses.
`reply()` stands in for the server's side of a request: its status, headers and content, or the network errors
that come first. That lets us test every kind of answer the service could give:

```python
import json
import socket

import httplib2

from PyMagic import run_model
from http_model import models
from http_model.magics import module_magics, reply
from stdlib_model.magics import stdlib_magics


def fortune():
    # today's fortune from the fortune cookie service, or a consolation
    try:
        response, content = httplib2.Http().request("https://cookies.example/fortune")
    except httplib2.ServerNotFoundError:
        return "the cookie jar is lost"
    if response.status != 200:
        return f"no cookie today ({response.status} {response.reason})"
    return json.loads(content)["fortune"]


def server(**answer):
    # every request gets the same reply, in an empty environment at a fixed time
    return {
        **module_magics({}), **stdlib_magics(now=1_700_000_000),
        httplib2.Http: {httplib2.Http.request: lambda self, uri, *args, **kwargs: reply(**answer)},
    }
```

```python
>>> run_model(models, server(content=b'{"fortune": "You will write a model today"}'))(fortune)
'You will write a model today'
>>> run_model(models, server(status=503))(fortune)
'no cookie today (503 Service Unavailable)'
>>> run_model(models, server(errors=[socket.gaierror(-2, "Name or service not known")]))(fortune)
'the cookie jar is lost'
```

No request leaves the machine. The model behaves as httplib2 does: the unknown host becomes a
`ServerNotFoundError`, and redirects are followed, so a generator that routes by `uri` can answer
`/fortune` with a `301` to `/fortune/today` and still get the fortune.

## Model the network: sockets

`stdlib_model` models `socket` too. A socket keeps the state Linux keeps for it: open or closed, bound, listening,
connected, shut down. So the errors that state decides are raised as Linux raises them: `recv()` on a closed socket
is `EBADF`, `send()` before `connect()` is `EPIPE`. What the network decides is magic: the descriptor a socket gets,
whether a connection is made, what a peer sends. Code over sockets, here `http.client`, runs unchanged against a
network written down:

```python
import http.client
import socket

from PyMagic import run_model
from stdlib_model import models
from stdlib_model.magics import network


def fortune():
    connection = http.client.HTTPConnection("cookies.example", timeout=5)
    connection.request("GET", "/fortune")
    return connection.getresponse().read().decode()


def cookie_jar(request):
    # the fortune cookie server: what it sends back to a request
    return b"HTTP/1.0 200 OK\r\n\r\nYou will write a model today"


web = network(hosts={"cookies.example": ["203.0.113.7"]}, peers={("203.0.113.7", 80): cookie_jar})
```

```python
>>> run_model(models, web)(fortune)
'You will write a model today'
>>> run_model(models, web)(lambda: socket.create_connection(("nowhere.example", 80)))
socket.gaierror: [Errno -2] Name or service not known
```

`network()` also takes clients that connect to a listening socket, and connects the two ends of a
`socketpair()`. `real_network()` asks the real kernel and network instead, with a real socket under each of the
model's. That is how the tests check the model against Linux: the same programs give the same outcomes on both.

## Compile a model

A model compiles to a package of Python modules: one for each module the models stand in for, and an
`__init__.py` that says which original each model stands in for. Compiled for a model checker, `magic()` is left
for the checker to supply, and constraints become asserts that fail with a `BrokenVariant` (with `--pure`, raises
of it). The package imports
nothing of the system a checker can't run, like `sys`, `os` or `socket`: what the models read of it, like
`sys.byteorder`, is written in as a literal, and a model that calls into it doesn't compile. With the dice game
saved as `dice.py`:

```bash
python3.10 -m PyMagic compile dice:randint -o out/compiled
```

That writes `out/compiled/random.py`:

```python
class BrokenVariant(BaseException):
    # magic() values the original couldn't give: the model rejects the call they make
    pass


def randint(a, b):
    face = magic(int)
    assert isinstance(face, int), BrokenVariant(f'face = magic(int) drew {face!r}, which is not int: the model of randint rejected this call')
    assert a <= face <= b, BrokenVariant('the values broke a <= face <= b: the model of randint rejected this call')
    return face
```

Nothing in the package puts the models in place: its `__init__.py` runs nothing and imports nothing, and only
has two tables, which whoever runs the package places the models by, and by nothing else:

```python
# (real module, the original's name there, compiled module, the model's name there)
REPLACED = [
    ('random', 'randint', '.random', 'randint'),
]

# (real module, compiled module)
STANDING_IN = []
```

- A compiled module is named relative to the package, and stands in for the real module of its name.
- `REPLACED` has a row for every name a standard library module binds an original to: the original's own name in
  its own module first, then its aliases, like `hashlib.md5` for `_hashlib.openssl_md5`, in the modules loaded
  while compiling, which are those the models and their originals import. The rows of a real
  module the package compiles name the model as they name the original: where the compiled `_socket` binds
  `socket`, it binds `SocketType` too.
- Compiled for a model checker, a compiled module of a C module also has the C module's constants, so that where
  there is no such C module, the compiled one stands in for it whole. A concrete package calls the real modules,
  which stay.
- A module in `STANDING_IN`, like `httplib2`, is stood in for whole, and so is every package above it.

Running the compiled model with `magic()` supplied, placed as a model checker would place it:

```python
import sys, builtins, importlib
sys.path.insert(0, "out")

landing = iter([3, 3, 9, 2])
builtins.magic = lambda *kinds: next(landing)   # what a model checker supplies

import random, compiled
for module, name, home, local in compiled.REPLACED:
    setattr(sys.modules[module], name, getattr(importlib.import_module(home, "compiled"), local))
from dice import play                           # random.randint is the compiled model now

play()    # 'doubles: you win a pizza'
play()    # AssertionError: the values broke a <= face <= b: ... (its message is a BrokenVariant)
```

The whole httplib2 model compiles the same way, into 17 modules that need neither httplib2 nor PyMagic:

```bash
python3.10 -m PyMagic compile http_model -o out/compiled
```

### No C specs, and pure Python

The standard library modules a compiled package imports may need C modules, which a model checker running Python
in Python doesn't have. `--no-C-spec` refuses a package that needs one, through any module it imports as that
module runs, and says by which imports it does:

```
PyMagic: with no C specs, the package can need no C module but those Python's own semantics rest on, and it needs
binascii, as httplib2/__init__.py imports base64, which imports binascii; ...
```

The C modules Python's own semantics rest on are allowed: `builtins`, `sys`, `_imp`, `marshal`, `_io`,
`_thread`, `_weakref`, `_warnings`, `_abc`, `_codecs`, `_string`, `_sre`, `itertools`, `_collections`,
`_functools` and `_operator`. An import a module goes on without, in a `try` that catches the `ImportError`, as
`heapq` goes on without `_heapq`, isn't needed; nor are those of functions, or of `if __name__ == "__main__":`.

`--pure` goes further, for a package that runs in pure Python: it has no C specs, every module is checked to
compile, and its `__init__.py` has no tables, only comments naming the real modules:

```python
# compiled from PyMagic models to run in pure Python, needing no C module but those Python's own semantics rest
# on. Nothing here runs: each compiled module stands in for the real module of its name, and holds the models of
# these real objects, under the names after the arrows
#
#   random.randint -> .random.randint
```

Its constraints are raises, not asserts, so that a broken one raises the `BrokenVariant` itself rather than an
`AssertionError` holding it, even under `python -O`:

```python
def randint(a, b):
    face = magic(int)
    if not isinstance(face, int):
        raise BrokenVariant(f'face = magic(int) drew {face!r}, which is not int: the model of randint rejected this call')
    if not a <= face <= b:
        raise BrokenVariant('the values broke a <= face <= b: the model of randint rejected this call')
    return face
```

The httplib2 model compiles pure: the models reach base64 and HMAC through the `binascii` and `_hashlib` models,
not through `base64` and `hmac`, which need C.

From Python, `compile_models(models, Compilation.MODEL_CHECKER)` returns the package as `{path: source}`, and
`write_package(files, directory)` writes it; `no_c_spec=True` and `pure=True` are the flags.

### Compile a spec

Specs go in the list with the models, or on their own. They compile for a model checker only: with the real effects,
a spec is checked as it is, under `run_spec`. A spec stands in for nothing, so it is compiled under its own name,
into the module named after its own. Nothing of PyMagic is left in it but exceptions: each `need()` raises
`ConditionalPropertyRaise`, and each `always()` raises `SpecPropertyInvalid`. With the specs of `max.py` in a list,
`specs = [max_property, witness_succ_exists, example_receive_existential]`:

```bash
python3.10 -m PyMagic compile max:specs --pure -o out/compiled
```

That writes `out/compiled/max.py`, which has:

```python
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


def witness_succ_exists(x):
    if not isinstance(x, int):
        raise ConditionalPropertyRaise("the arguments don't meet need(isinstance(x, int)): witness_succ_exists says nothing of them")
    try:
        return exists(lambda y: y > x, x + 1)
    except ConditionalPropertyRaise as _unmet:
        raise SpecPropertyInvalid(f'witness_succ_exists relies on what another spec says nothing of: {_unmet}') from _unmet
```

The checker supplies the arguments of a spec, every one it can: that is the "for all". A `ConditionalPropertyRaise`
leaves them out, and a `SpecPropertyInvalid` is a counterexample. Code that calls anything runs in a `try`, so that
when another spec it reaches rules out what it's given, that's a counterexample to this one, as it is when the spec
runs. Only the builtins that neither call nor iterate what they're given, like `isinstance`, go without one: `max`
gets one, as it can be given a `key` to call or a generator to run. `_pymagic.py` has the exceptions, and `exists` in plain Python. A pure package's `__init__.py`
names the specs after the models:

```python
#   (nothing)
#
# and these specs, each a property of all its arguments: arguments a need() rules out raise a
# ConditionalPropertyRaise, and a counterexample raises a SpecPropertyInvalid
#
#   max.max_property -> .max.max_property
#   max.witness_succ_exists -> .max.witness_succ_exists
#   max.example_receive_existential -> .max.example_receive_existential
```

Without `--pure`, they are in a `SPECS` table, with rows like those of `REPLACED`.

- A spec that a compiled spec calls is compiled with it. What a spec uses of its project is compiled into the
  module named after the one that defines it.
- A spec doesn't compile if it calls `constraint()`, another spec's `forall()`, or a model function by its own
  name: only a spec that runs has those. A model is reached through the original it stands in for.
- `need()` compiles only as a statement of the spec's own body, not inside a block or a nested function, so that it
  rules the arguments out before the rest of the spec runs. `always()` compiles anywhere in the spec's own code, but
  not in a function the spec calls.
- With `--pure`, the models' constraints are raises too, so the package has no `assert`.

# Command line

```
pymagic compile MODELS [-o DIRECTORY] [--concrete] [--no-C-spec] [--pure] [--force]
```

`pymagic` is installed with the package; `python3.10 -m PyMagic` does the same.

- `MODELS` is `module:attribute`, naming a model, a spec, or a list of them, like `dice:randint`,
  `stdlib_model.random:_model_random` or `max:specs`. A module alone, like `http_model`, compiles its `models` list.
- Without `-o`, the package's files are printed. With `-o DIRECTORY`, they're written there. A directory that
  isn't empty is written into only with `--force`, which overwrites the files it writes and keeps the others.
- `--concrete` compiles the models to call the real effects in place of `magic()`, checking each result against
  the model, instead of compiling them for a model checker.
- `--no-C-spec` refuses a package that needs a C module, but those Python's own semantics rest on.
- `--pure` compiles a package to run in pure Python: it has no C specs, its constraints raise rather than assert,
  and its `__init__.py` names the real modules in comments alone.
- If the models can't be compiled, the reason is printed and the exit code is 1. If they can't be found, the exit
  code is 2. `python3.10 -m PyMagic compile --help` shows this too.

# What's here

- `PyMagic/`: the library. `model.py` has `@model`, `magic()` and `constraint()`; `run.py` has `run_model` and
  `run_spec`; `specs.py` has `@spec`, `need()`, `always()` and `exists`; `compile.py` has `compile_models`; `cli.py`
  has the command line.
- `http_model/`: a model of httplib2, laid out as httplib2 is.
- `stdlib_model/`: models of the standard library modules the httplib2 model reaches, and of sockets, a folder
  for each. `stdlib_model/magics.py` has the magics that answer them: a clock, a seeded random generator, and a
  network, written down or real.
- `effecful.py`: small examples of models of `random.randint` and `input`.

# License

MIT, see `LICENSE`. The models copy code from httplib2 (MIT) and CPython (PSF License); `NOTICES.md` has their
licenses and what was changed.
