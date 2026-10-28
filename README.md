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
`__init__.py` that installs them in place of the originals. Compiled for a model checker, `magic()` is left for
the checker to supply, and constraints become asserts that fail with a `BrokenVariant`. With the dice game saved
as `dice.py`:

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

Running the compiled model with `magic()` supplied, as a model checker would supply it:

```python
import sys, builtins
sys.path.insert(0, "out")

landing = iter([3, 3, 9, 2])
builtins.magic = lambda *kinds: next(landing)   # what a model checker supplies

import compiled                                 # random.randint is the compiled model now
from dice import play

play()    # 'doubles: you win a pizza'
play()    # AssertionError: the values broke a <= face <= b: ... (its message is a BrokenVariant)
```

The whole httplib2 model compiles the same way, into 17 modules that need neither httplib2 nor PyMagic:

```bash
python3.10 -m PyMagic compile http_model -o out/compiled
```

From Python, `compile_models(models, Compilation.MODEL_CHECKER)` returns the package as `{path: source}`, and
`write_package(files, directory)` writes it.

# Command line

```
pymagic compile MODELS [-o DIRECTORY] [--concrete] [--force]
```

`pymagic` is installed with the package; `python3.10 -m PyMagic` does the same.

- `MODELS` is `module:attribute`, naming a model or a list of them, like `dice:randint` or
  `stdlib_model.random:_model_random`. A module alone, like `http_model`, compiles its `models` list.
- Without `-o`, the package's files are printed. With `-o DIRECTORY`, they're written there. A directory that
  isn't empty is written into only with `--force`, which overwrites the files it writes and keeps the others.
- `--concrete` compiles the models to call the real effects in place of `magic()`, checking each result against
  the model, instead of compiling them for a model checker.
- If the models can't be compiled, the reason is printed and the exit code is 1. If they can't be found, the exit
  code is 2. `python3.10 -m PyMagic compile --help` shows this too.

# What's here

- `PyMagic/`: the library. `model.py` has `@model`, `magic()` and `constraint()`; `run.py` has `run_model`;
  `compile.py` has `compile_models`; `cli.py` has the command line.
- `http_model/`: a model of httplib2, laid out as httplib2 is.
- `stdlib_model/`: models of the standard library modules the httplib2 model reaches, and of sockets, a folder
  for each. `stdlib_model/magics.py` has the magics that answer them: a clock, a seeded random generator, and a
  network, written down or real.
- `effecful.py`: small examples of models of `random.randint` and `input`.

# License

MIT, see `LICENSE`. The models copy code from httplib2 (MIT) and CPython (PSF License); `NOTICES.md` has their
licenses and what was changed.
