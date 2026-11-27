# models and the @model decorator: what a model is, the magic() values it draws and the constraint() calls
# that reject the calls its original couldn't make

import ast
import dis
import sys
import types
import builtins
import textwrap
import linecache
import functools
import dataclasses
from enum import Enum, auto
from typing import Any, Iterator
from contextvars import ContextVar
from inspect import signature, isroutine, isclass, unwrap, getsourcelines


# ------------------------------------------------------------------ errors

class RunningUnstatedModel(BaseException):
    pass

class BrokenVariant(BaseException):
    # magic() values the original couldn't give: the model rejects the call they make
    pass

class MissedModelObject(BaseException):
    pass

def name_of(obj):
    return getattr(obj, '__qualname__', obj)

# whether a message is showing the arguments of a call: a repr() that runs models, as a socket's does, then shows
# the arguments of their calls plainly
showing = ContextVar("showing", default=False)

def shown(value):
    # value's repr() for a message, or the plain one when it fails or a repr() is showing already
    if showing.get():
        return object.__repr__(value)
    token = showing.set(True)
    try:
        return repr(value)
    except BaseException:
        return object.__repr__(value)
    finally:
        showing.reset(token)

def format_call(func, args, kwargs):
    params = [shown(arg) for arg in args] + [f"{key}={shown(value)}" for key, value in kwargs.items()]
    return f"{name_of(func)}({', '.join(params)})"


# ------------------------------------------------------------------ models

class Model(Enum):
    CLASS = auto()     # @model(SomeClass) on a class: each of its methods replaces that method of the class
    COPY = auto()      # @model(SomeClass) on a class named SomeClass, or on an exception: a copy that replaces
                       # SomeClass everywhere
    MODULE = auto()    # @model(module) on a class: each @model(function) in it replaces that function everywhere
    FUNCTION = auto()  # @model(function) on a function: replaces that function everywhere
    PATCHES = auto()   # a plain {function: replacement} dict: each function is replaced as is
    SPEC = auto()      # @spec on a function: a property of all its arguments, which models nothing; it is compiled
                       # beside the models, and run_model has nothing to put in place for it

def signature_of(original):
    try:
        return signature(original) if callable(original) else None
    except (TypeError, ValueError):
        # builtins like max have no signature to check calls against
        return None

# the objects the models only name, by id: an identity, or a class for isinstance() that no model makes
named_only = {}

def named(original):
    # marks an object the models only name: an identity, a class for isinstance(), or an exception they raise as
    # it is. In a run it is the original, and compiled for a model checker it is a stand-in for it, an object(), an
    # empty class or an exception class with the original's bases, so the compiled models don't import the module
    # it comes from
    named_only[id(original)] = original
    return original

def model(original):
    # marks a class or a function as the model of original; a function model must be called like the
    # original, and its magic(kind) and constraint() calls reject the calls the original couldn't make
    if original is None:
        raise MissedModelObject(
            "model() needs the original object to stand in for (a function, class or module), got None"
        )
    original_signature = signature_of(original)

    def decorator(instance):
        if isclass(instance):
            instance.__model__ = original
            if not isclass(original):
                instance.__model_kind__ = Model.MODULE
            elif instance.__name__ == original.__name__ or (
                issubclass(original, BaseException) and issubclass(instance, BaseException)
            ):
                instance.__model_kind__ = Model.COPY
            else:
                instance.__model_kind__ = Model.CLASS
            return instance

        if callable(instance):
            return stand_in(instance, original, original_signature, magic_generator=None)

        raise MissedModelObject(
            f"@model({name_of(original)!s}) can only decorate a class or a function, "
            f"got {instance!r} of type {type(instance).__name__}"
        )
    return decorator

def get_models(models):
    # (kind, model) for each entry of a models list and each @model class nested in one, like the model of a
    # class inside the model of its module; a class without @model works like a module model
    for entry in models:
        if isinstance(entry, dict):
            yield Model.PATCHES, entry
        else:
            yield getattr(entry, "__model_kind__", Model.MODULE), entry
            yield from get_models(nested_models(entry))

def nested_models(model_class):
    return [
        member for name, member in vars(model_class).items()
        if name != "__model__" and isclass(member) and "__model_kind__" in vars(member)
    ]

def model_members(model_class):
    # (attribute name, member as stored in the class, original it models or None) for each function in it
    for name, member in vars(model_class).items():
        if isroutine(member):
            yield name, member, getattr(getattr(model_class, name), "__model__", None)


# ------------------------------------------------------------------ magic values

@dataclasses.dataclass
class ByPosition:
    # every magic() takes the next value, in the order the calls run
    values: Iterator

@dataclasses.dataclass
class ByName:
    # every `name = magic()` takes the next value from its own variable's iterator
    values: dict

@dataclasses.dataclass
class ModelCall:
    # a model function call in progress, the magic values its magic() calls draw from and those drawn so far
    values: Any  # ByPosition, ByName, or None when no magics were given
    func: Any
    original: Any
    args: tuple
    kwargs: dict
    drawn: list = dataclasses.field(default_factory=list)

    def __str__(self):
        return format_call(self.func, self.args, self.kwargs)

# the model calls in progress, innermost last
running_models = ContextVar("running_models", default=())

# the copies of classes in the running models, by the original each copies
running_copies = ContextVar("running_copies", default={})

OSERROR_FIELDS = ("errno", "strerror", "filename", "filename2")

def transplanted(error, kind):
    # error as an exception of kind, an original or its copy: its arguments, attributes, cause, context and
    # traceback, set as they are rather than by kind's __init__
    made = kind.__new__(kind, *error.args)
    made.args = error.args
    if isinstance(error, OSError):
        for field in OSERROR_FIELDS:
            if getattr(error, field) is not None:  # an unset one reads None, but set to None it shows in str()
                setattr(made, field, getattr(error, field))
    vars(made).update(vars(error))
    made.__cause__, made.__context__ = error.__cause__, error.__context__
    made.__suppress_context__ = error.__suppress_context__
    return made.with_traceback(error.__traceback__)

def as_copy(value):
    # a magic value that is an original exception, as its copy in the run
    copy = running_copies.get().get(type(value))
    if copy is None or not isinstance(value, BaseException):
        return value
    return transplanted(value, copy)

LOADS = {"LOAD_GLOBAL", "LOAD_NAME", "LOAD_DEREF", "LOAD_FAST"}
ATTRIBUTE_LOADS = {"LOAD_METHOD", "LOAD_ATTR"}  # PyMagic.magic
CALLS = {"CALL_FUNCTION", "CALL_METHOD", "CALL"}
STORES = {"STORE_FAST", "STORE_NAME", "STORE_GLOBAL", "STORE_DEREF"}
SKIPPED = {"CACHE", "PUSH_NULL", "PRECALL"}  # no-ops around calls in Python 3.11+

@functools.lru_cache(maxsize=None)
def instructions_of(code):
    return [instruction for instruction in dis.get_instructions(code) if instruction.opname not in SKIPPED]

@functools.lru_cache(maxsize=None)
def assigned_calls(code):
    # {offset of a call: variable} for every call whose result goes straight into a variable: `x = f()`
    instructions = instructions_of(code)
    return {
        call.offset: store.argval
        for call, store in zip(instructions, instructions[1:])
        if call.opname in CALLS and store.opname in STORES
    }

def code_objects(code):
    yield code
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            yield from code_objects(const)

def collect_magic(func):
    # the variables assigned straight from magic() in func and the functions nested in it: `x = magic(int)`
    func = unwrap(func)
    names = []
    for code in code_objects(func.__code__):
        stores = assigned_calls(code)
        magic_load = None
        for instruction in instructions_of(code):
            loads_magic = (instruction.opname in LOADS | ATTRIBUTE_LOADS and instruction.argval == magic.__name__) or (
                instruction.opname in LOADS and func.__globals__.get(instruction.argval) is magic
            )
            if loads_magic:
                magic_load = instruction
            elif instruction.opname in CALLS:
                # the first call after loading magic is magic's own, unless its argument makes a call itself
                if magic_load is not None and instruction.offset in stores:
                    names.append(stores[instruction.offset])
                magic_load = None
    return list(dict.fromkeys(names))

def kind_name(kind):
    return getattr(kind, "__name__", repr(kind))

def magic_text(kinds):
    return f"magic({', '.join(map(kind_name, kinds))})"

def call_site(frame, kinds):
    # (variable, description) of the magic() call the frame is running; `x = magic(int)` gives "x"
    name = assigned_calls(frame.f_code).get(frame.f_lasti)
    text = magic_text(kinds) if name is None else f"{name} = {magic_text(kinds)}"
    return name, f"{text} ({frame.f_code.co_filename}:{frame.f_lineno})"

def swap(values, call, place):
    # the value that stands in for a magic() call: the next one of its iterator
    try:
        value = next(values)
    except StopIteration:
        raise RunningUnstatedModel(
            f"{call} ran out of magic values at {place}; its magic generator stopped "
            f"before giving one (use itertools.cycle([...]) to repeat values)"
        ) from None
    call.drawn.append(value)
    return value

def magic(*kinds):
    # a value the model doesn't know, supplied by the magics; magic(str, int) is sugar for
    # x = magic() followed by constraint(isinstance(x, (str, int)))
    running = running_models.get()
    if not running:
        raise RunningUnstatedModel(
            f"{magic_text(kinds)} was called with no model state to draw a value from; "
            f"magic() only produces values while a model is being run"
        )
    call = running[-1]
    name, place = call_site(sys._getframe(1), kinds)

    match call.values:
        case None:
            raise RunningUnstatedModel(
                f"{call} reached {place}, but no magics were given for it; pass them as run_model(models, magics), "
                f"or run_spec(models, magics) for a spec"
            )
        case ByPosition(values):
            pass
        case ByName() if name is None:
            raise RunningUnstatedModel(
                f"{call} reached {place}, which is not assigned straight to a variable, "
                f"so values by variable name can't reach it; write `name = magic()` or have the magic generator "
                f"return an iterable instead of a dict"
            )
        case ByName(iterators) if name not in iterators:
            raise RunningUnstatedModel(
                f"{call} assigns {name} = magic() but its magic generator gave no values "
                f"for {name!r} (given: {sorted(iterators)})"
            )
        case ByName(iterators):
            values = iterators[name]

    value = as_copy(swap(values, call, place))
    try:
        of_kind, asking = not kinds or isinstance(value, kinds), None
    except (AttributeError, TypeError) as error:
        of_kind, asking = False, error  # a value whose kind can't even be asked is none of them
    if not of_kind:
        raise BrokenVariant(
            f"{call} drew {shown(value)} at {place}, which is not {' or '.join(map(kind_name, kinds))}: "
            f"the model of {name_of(call.original)} rejected this call"
        ) from asking
    return value

def source_at(frame, default):
    # the line of source a frame is running and where it is, `constraint(a <= face <= b) (dice.py:20)`, with
    # default for the line when its source isn't available
    source = linecache.getline(frame.f_code.co_filename, frame.f_lineno).strip() or default
    return f"{source} ({frame.f_code.co_filename}:{frame.f_lineno})"

# ------------------------------------------------------------------ conditions that can't be asked

def resolve(func, expression):
    # the object a name or dotted name in func's code refers to at module level, or None
    match expression:
        case ast.Name(id=name):
            return func.__globals__.get(name, getattr(builtins, name, None))
        case ast.Attribute(value=owner, attr=attribute):
            owner = resolve(func, owner)
            return None if owner is None else getattr(owner, attribute, None)
        case _:
            return None

def kindless_magic(tree, is_magic):
    # the variables a model's code assigns straight from a magic() of no kind: their values are any object at all
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and is_magic(node.value) and not node.value.args:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names |= {name.id for target in targets for name in ast.walk(target) if isinstance(name, ast.Name)}
    return names

def asks_any_object(condition, kindless, is_magic):
    # whether a condition reads a value of magic() of no kind, any object, which it may not even be asked of
    return any(
        isinstance(node, ast.Name) and node.id in kindless or is_magic(node) and not node.args
        for node in ast.walk(condition)
    )

@functools.lru_cache(maxsize=None)
def check_lines(func, target):
    # {line: whether its condition reads any object} for each target() statement, constraint() or need(), in the
    # source of func and the functions nested in it, or {} when its source isn't available
    try:
        lines, first = getsourcelines(func)
        tree = ast.parse(textwrap.dedent("".join(lines)))
    except (OSError, TypeError, SyntaxError):
        return {}
    is_magic = lambda node: isinstance(node, ast.Call) and resolve(func, node.func) is magic
    kindless = kindless_magic(tree, is_magic)
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) and node.value.args and \
                resolve(func, node.value.func) is target:
            asks = asks_any_object(node.value.args[0], kindless, is_magic)
            for line in range(node.lineno, node.end_lineno + 1):
                found[first + line - 1] = asks
    return found

def unasked(func, traceback, target, any_object_only):
    # where func's own code was asking the condition of a target() statement when the error of traceback was raised,
    # `constraint(x.balance >= 0) (bank.py:12)`, or None: the innermost of func's frames must be on such a
    # statement, one whose condition reads any object if any_object_only
    func = unwrap(func)
    codes = set(code_objects(func.__code__))
    innermost = None
    while traceback is not None:
        if traceback.tb_frame.f_code in codes:
            innermost = traceback
        traceback = traceback.tb_next
    if innermost is None:
        return None
    asks = check_lines(func, target).get(innermost.tb_lineno)
    if asks is None or any_object_only and not asks:
        return None
    filename = innermost.tb_frame.f_code.co_filename
    return f"{linecache.getline(filename, innermost.tb_lineno).strip()} ({filename}:{innermost.tb_lineno})"

def constraint(condition):
    # the magic values drawn so far must make the condition true, otherwise the call is not one the
    # original could make
    if condition:
        return
    where = source_at(sys._getframe(1), "constraint(...)")
    running = running_models.get()
    if not running:
        raise BrokenVariant(f"{where} failed outside any running model")
    call = running[-1]
    raise BrokenVariant(
        f"{call} drew {call.drawn} and broke {where}: the model of {name_of(call.original)} rejected this call"
    )

def magic_values(generator, func, names, args, kwargs):
    # the generator gets the call's arguments and returns either any iterable (a list, itertools.cycle,
    # a generator...) of values by position, or a dict of such iterables by variable name
    values = generator(*args, **kwargs)
    match values:
        case dict():
            unknown = set(values) - set(names)
            if unknown:
                raise RunningUnstatedModel(
                    f"the magic generator for {format_call(func, args, kwargs)} gave values for {sorted(unknown)}, "
                    f"but {func.__qualname__} only assigns magic() to {names}"
                )
            iterators = {}
            for name, value in values.items():
                try:
                    iterators[name] = iter(value)
                except TypeError:
                    raise RunningUnstatedModel(
                        f"the magic generator for {format_call(func, args, kwargs)} gave {name!r}: {value!r}; "
                        f"each variable needs an iterable of values, e.g. {{{name!r}: itertools.cycle([{value!r}])}}"
                    ) from None
            return ByName(iterators)
        case _:
            try:
                return ByPosition(iter(values))
            except TypeError:
                raise RunningUnstatedModel(
                    f"the magic generator for {format_call(func, args, kwargs)} returned {values!r}; "
                    f"it must return a dict by variable name or an iterable of values, e.g. itertools.cycle([...])"
                ) from None

def stand_in(func, original, original_signature, magic_generator):
    # func as it is called in place of original: the call must be valid for the original, and every magic()
    # in func draws from magic_generator's values for that call (without one, magic() fails saying no
    # magics were given); whatever func returns is the result
    names = collect_magic(func)

    @functools.wraps(func)
    def modelled(*args, **kwargs):
        if original_signature is not None:
            try:
                original_signature.bind(*args, **kwargs)
            except TypeError as error:
                raise TypeError(
                    f"{format_call(func, args, kwargs)} does not match the signature of the "
                    f"original {name_of(original)}{original_signature}: {error}"
                ) from error
        values = None if magic_generator is None else magic_values(magic_generator, func, names, args, kwargs)
        call = ModelCall(values, func, original, args, kwargs)
        token = running_models.set(running_models.get() + (call,))
        try:
            return func(*args, **kwargs)
        except (AttributeError, TypeError) as error:
            # a constraint that reads a value of magic() of no kind, any object, can't be asked of one without what
            # it asks for: such a value breaks it, as one it is false of does
            where = unasked(func, error.__traceback__, constraint, any_object_only=True)
            if where is None:
                raise
            raise BrokenVariant(
                f"{call} drew {call.drawn} and broke {where}, which can't be asked of them: the model of "
                f"{name_of(original)} rejected this call"
            ) from error
        finally:
            running_models.reset(token)

    modelled.__model__ = original
    modelled.__model_kind__ = Model.FUNCTION
    return modelled
