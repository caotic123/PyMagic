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

# LLM iteration

Just simply asking the LLM to find bugs using models/compiled models is enough to find bugs that would be hidden if no model is designed. Models allow them to reason about specs (Although vibe-coding models without review is not preferred over reviewed human models). The first step is just starting to refine a model. Mocking real values that mimic the effectful/magic computation is the second phase. In fact, this substantially increases the debugging surface, since it make clear to the LLM iteration that a proper spec should be written and respect. 


