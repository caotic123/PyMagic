# compile_models and its helpers: writing models out as Python source, for a model checker or with the
# real effects in place of magic()

import ast
import sys
import types
import inspect
import builtins
import textwrap
import functools
import itertools
import sysconfig
import importlib.util
import importlib.machinery
from enum import Enum, auto
from collections import deque
from inspect import Parameter, signature, isclass, unwrap, findsource

from .model import (
    BrokenVariant, Model, code_objects, constraint, get_models, magic, model, model_members, name_of, named_only,
    signature_of,
)


class UncompilableModel(BaseException):
    pass


class Compilation(Enum):
    MODEL_CHECKER = auto()  # magic() stays undefined, for the model checker to supply
    CONCRETE = auto()       # magic() takes its values from the real effect the model stands in for

COMPILED_HEADERS = {
    Compilation.MODEL_CHECKER: "# compiled from PyMagic models for a model checker, which supplies magic()\n"
                               "# magic() values breaking a constraint fail its assert with a BrokenVariant",
    Compilation.CONCRETE: "# compiled from PyMagic models, with the real effects in place of magic()",
}

# names the concrete code gives the real result, the iterator of its magic values, the real exception and
# the helper taking the real result's elements
CONCRETE_NAMES = {"_magic", "_result", "_error", "_elements"}

# written into a concretely compiled module when a model's magic() calls take the real result's elements
ELEMENTS_HELPER = '''\
def _elements(result, name):
    # the elements of the real result, for its model's magic() calls to take in order
    try:
        values = iter(result)
    except TypeError:
        raise AssertionError(f"the real {name} returned {result!r}, which has no elements for its model's magic() calls") from None
    yield from values
    raise AssertionError(f"its model's magic() calls drew more values than the real {name} returned in {result!r}")'''

LOOPS = (ast.For, ast.AsyncFor, ast.While, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)

def magic_variable(statement, calls_magic):
    # the variable of a `name = magic()` or `name: T = magic()` statement, otherwise None
    match statement:
        case ast.Assign(targets=[ast.Name(id=name)], value=value) | ast.AnnAssign(target=ast.Name(id=name), value=value):
            return name if calls_magic(value) else None
        case _:
            return None

def own_nodes(node):
    # the nodes of a function's own code, without the functions and classes nested in it
    for child in ast.iter_child_nodes(node):
        yield child
        if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            yield from own_nodes(child)

def function_node(func):
    # the syntax tree of func's definition, found in its whole source file by its first line
    try:
        lines, _ = findsource(func)
    except (OSError, TypeError):
        raise UncompilableModel(f"the source of {func.__qualname__} is not available to compile it") from None
    code = func.__code__
    for node in ast.walk(ast.parse("".join(lines))):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == code.co_name:
            if min([node.lineno] + [decorator.lineno for decorator in node.decorator_list]) == code.co_firstlineno:
                return node
    raise UncompilableModel(f"the definition of {func.__qualname__} was not found in its source to compile it")

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

def reference_to(original):
    # (module to import, source text) that refers to original from the compiled module
    module = sys.modules.get(getattr(original, "__module__", None))
    if module is not None:
        for name, value in vars(module).items():
            if value is original:
                return module.__name__, f"{module.__name__}.{name}"
        path = getattr(original, "__qualname__", "")
        target = module
        for part in path.split("."):
            target = getattr(target, part, None)
        if path and target is original:
            return module.__name__, f"{module.__name__}.{path}"
    raise UncompilableModel(
        f"{name_of(original)!s} can't be referred to from the compiled module, so it can't be called in place of magic()"
    )

# where installed packages live: what is defined there is imported, not written into the compiled module
INSTALLED = tuple(sysconfig.get_paths()[key] for key in ("purelib", "platlib"))

def is_standard(module_name):
    return module_name == "builtins" or module_name.split(".")[0] in sys.stdlib_module_names

# the standard library modules of the running process and the system around it, which a model checker can't run:
# a module compiled for one takes their constants as literals, and imports nothing of them
SYSTEM_MODULES = {
    "sys", "os", "posix", "nt", "importlib", "subprocess", "signal", "threading", "_thread", "multiprocessing",
    "socket", "_socket", "ssl", "select", "selectors", "time", "random",
}

def is_system(module_name):
    return module_name.split(".")[0] in SYSTEM_MODULES

def is_literal(value):
    # whether value's repr is a literal that evaluates back to it
    try:
        again = ast.literal_eval(repr(value))
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        return False
    return type(again) is type(value) and again == value

def is_constant(value):
    # a literal that can't change, which a model checker can have as it is wherever it's used
    return is_literal(value) and not isinstance(value, (dict, list, set))

def home_of(original):
    # the module an original belongs to: the one it names, unless that wraps the C module under it, which defines
    # the original under the wrapper's name, as _socket defines socket.gaierror; then the C module, which the
    # wrapper's compiled module imports from as the wrapper does
    named = original.__module__
    beneath = sys.modules.get("_" + named) if "." not in named else None
    return "_" + named if getattr(beneath, original.__name__, None) is original else named

def is_value(value):
    # whether a model checker needs value as it is, written out rather than stood in for: a literal, or an
    # enumeration or a member of one
    return is_literal(value) or isinstance(value, Enum) or isinstance(value, type) and issubclass(value, Enum)

def standard_home(value, name):
    # (module, attribute) of the standard library module that holds value, its public module first, or None
    owner = getattr(value, "__module__", None)
    if isinstance(owner, str) and not is_standard(owner):
        return None
    # the module it says it belongs to first, then any (types.NoneType says builtins, which doesn't hold it)
    modules = [owner.lstrip("_"), owner] if isinstance(owner, str) else []
    modules += sorted(module for module in list(sys.modules) if is_standard(module) and not module.startswith("_"))
    for module_name in modules:
        names = [key for key, held in vars(sys.modules.get(module_name, types)).items() if held is value]
        if names and module_name in sys.modules:
            # the name it's used by, else its own, else the most public one
            own = getattr(value, "__name__", None)
            return module_name, name if name in names else own if own in names else \
                min(names, key=lambda key: (key.startswith("_"), key))
    return None

def bound_names(tree):
    # the names a definition binds itself: its parameters, assignments, imports and nested definitions
    bound = set()
    for node in ast.walk(tree):
        match node:
            case ast.Name(id=name, ctx=ast.Store() | ast.Del()) | ast.arg(arg=name):
                bound.add(name)
            case ast.FunctionDef(name=name) | ast.AsyncFunctionDef(name=name) | ast.ClassDef(name=name):
                bound.add(name)
            case ast.alias(name=module, asname=alias):
                bound.add(alias or module.split(".")[0])
            case ast.ExceptHandler(name=name) if name:
                bound.add(name)
    return bound

def passed_on(arguments):
    # (args, keywords) of a call passing on every parameter of a definition as it was received
    args = [ast.Name(arg.arg, ast.Load()) for arg in arguments.posonlyargs + arguments.args]
    if arguments.vararg:
        args.append(ast.Starred(ast.Name(arguments.vararg.arg, ast.Load()), ast.Load()))
    keywords = [ast.keyword(arg.arg, ast.Name(arg.arg, ast.Load())) for arg in arguments.kwonlyargs]
    if arguments.kwarg:
        keywords.append(ast.keyword(None, ast.Name(arguments.kwarg.arg, ast.Load())))
    return args, keywords

def default_text(parameter):
    return "no default" if parameter.default is Parameter.empty else f"the default {parameter.default!r}"

def same_default(mine, theirs):
    if mine is theirs:
        return True
    try:
        return bool(mine == theirs)
    except Exception:
        return False

def differing_default(func, original):
    # the first (model parameter, original parameter) whose defaults differ, matched by position and by
    # keyword-only name, or None
    original_signature = signature_of(original)
    if original_signature is None:
        return None

    def split(parameters):
        positional = [p for p in parameters if p.kind in (Parameter.POSITIONAL_ONLY, Parameter.POSITIONAL_OR_KEYWORD)]
        return positional, {p.name: p for p in parameters if p.kind is Parameter.KEYWORD_ONLY}

    model_positional, model_keyword = split(signature(func).parameters.values())
    original_positional, original_keyword = split(original_signature.parameters.values())
    pairs = [
        *zip(model_positional, original_positional),
        *[(parameter, original_keyword[name]) for name, parameter in model_keyword.items() if name in original_keyword],
    ]
    return next(((mine, theirs) for mine, theirs in pairs if not same_default(mine.default, theirs.default)), None)

def kind_assertion(name, kinds, flag=None):
    # `assert isinstance(name, kind)` for a magic variable drawn with kinds, a tuple of them for several, failing
    # with flag when there's one
    if not kinds:
        return []
    kind = kinds[0] if len(kinds) == 1 else ast.Tuple(kinds, ast.Load())
    return [ast.Assert(ast.Call(ast.Name("isinstance", ast.Load()), [ast.Name(name, ast.Load()), kind], []), flag)]

def rebinds(node, name, magic_statement):
    # whether the model's own code binds name anywhere besides its `name = magic()` statement
    target = magic_statement.targets[0] if isinstance(magic_statement, ast.Assign) else magic_statement.target
    for child in own_nodes(node):
        match child:
            case ast.Name(id=bound, ctx=ast.Store() | ast.Del()) if bound == name and child is not target:
                return True
            case (ast.ExceptHandler(name=bound) | ast.FunctionDef(name=bound) | ast.AsyncFunctionDef(name=bound)
                  | ast.ClassDef(name=bound)) if bound == name:
                return True
            case ast.alias(name=module, asname=alias) if (alias or module.split(".")[0]) == name:
                return True
            case ast.Global(names=bound) | ast.Nonlocal(names=bound) if name in bound:
                return True
    return False

def in_loop(node, call):
    # whether a call in the model's own code is inside a loop or comprehension, so it can run many times
    return any(
        isinstance(child, LOOPS) and any(inner is call for inner in ast.walk(child)) for child in own_nodes(node)
    )

def holding_statement(statements, call, enclosing=()):
    # (the block, the index of the statement in it, the compound statements around) of the innermost statement
    # holding a call
    for index, statement in enumerate(statements):
        if not any(inner is call for inner in ast.walk(statement)):
            continue
        blocks = [getattr(statement, field) for field in ("body", "orelse", "finalbody")
                  if isinstance(getattr(statement, field, None), list)]
        blocks += [handler.body for handler in getattr(statement, "handlers", [])]
        blocks += [case.body for case in getattr(statement, "cases", [])]
        for block in blocks:
            if any(inner is call for inner_statement in block for inner in ast.walk(inner_statement)):
                return holding_statement(block, call, (*enclosing, statement))
        return statements, index, enclosing
    return None

def draws_at_most_once(node, magic_calls):
    # whether a run of the model reaches at most one of its magic() calls: each outside loops and try or with
    # blocks, alone in its statement, with the statements after it in its block drawing none and ending the run
    calls = {id(call) for call in magic_calls}
    for call in magic_calls:
        held = holding_statement(node.body, call)
        if held is None or in_loop(node, call):
            return False
        block, index, enclosing = held
        if any(isinstance(statement, (ast.Try, ast.With, ast.AsyncWith, *LOOPS)) for statement in enclosing):
            return False
        statement, rest = block[index], block[index + 1:]
        if sum(id(inner) in calls for inner in ast.walk(statement)) != 1:
            return False
        if any(id(inner) in calls for later in rest for inner in ast.walk(later)):
            return False
        if not isinstance((rest or [statement])[-1], (ast.Return, ast.Raise)):
            return False
    return True

def raised_types(node, func):
    # the exception classes a model raises in its own code, or None when one of its raises could be anything
    raised = {}
    for statement in own_nodes(node):
        if isinstance(statement, ast.Raise):
            exception = statement.exc.func if isinstance(statement.exc, ast.Call) else statement.exc
            value = None if exception is None else resolve(func, exception)
            if not (isclass(value) and issubclass(value, BaseException)):
                return None
            raised.setdefault(ast.unparse(exception), exception)
    return list(raised.values())

def f_string(*parts):
    # an f-string of text and !r-formatted expressions
    return ast.JoinedStr([
        ast.Constant(part) if isinstance(part, str) else ast.FormattedValue(part, ord("r"), None) for part in parts
    ])

def broken_variant(*parts):
    # `BrokenVariant(f"...")`, the message of a constraint's assert in a module compiled for a model checker
    text = all(isinstance(part, str) for part in parts)
    return ast.Call(ast.Name("BrokenVariant", ast.Load()), [ast.Constant("".join(parts)) if text else f_string(*parts)], [])

def assertion_error(*parts, cause=None):
    # `raise AssertionError(f"...")`, from cause
    return ast.Raise(ast.Call(ast.Name("AssertionError", ast.Load()), [f_string(*parts)], []), cause)

def real_call_statements(target, real_call, allowed, name):
    # `target = real_call`, letting through the exceptions the model raises too, and failing on any other
    assign = ast.Assign([target], real_call)
    if allowed is None:
        return [assign]
    handlers = []
    if allowed:
        handlers.append(ast.ExceptHandler(
            allowed[0] if len(allowed) == 1 else ast.Tuple(allowed, ast.Load()), None, [ast.Raise(None, None)]
        ))
    error = ast.Name("_error", ast.Load())
    handlers.append(ast.ExceptHandler(ast.Name("Exception", ast.Load()), "_error", [
        assertion_error(f"the real {name} raised ", error, ", which its model never raises", cause=error)
    ]))
    return [ast.Try([assign], handlers, [], [])]

def unpack_statements(names, name):
    # `(q, r) = _result`, failing when the real result doesn't unpack into the model's magic values
    target = ast.Tuple([ast.Name(variable, ast.Store()) for variable in names], ast.Store())
    result = ast.Name("_result", ast.Load())
    message = (f"the real {name} returned ", result,
               f", which doesn't unpack into its model's magic values ({', '.join(names)})")
    handler = ast.ExceptHandler(
        ast.Tuple([ast.Name("TypeError", ast.Load()), ast.Name("ValueError", ast.Load())], ast.Load()), None,
        [assertion_error(*message, cause=ast.Constant(None))],
    )
    return [ast.Try([ast.Assign([target], result)], [handler], [], [])]

def next_magic():
    return ast.Call(ast.Name("next", ast.Load()), [ast.Name("_magic", ast.Load())], [])

def as_list(visited):
    # what visiting a statement gave: a list of statements, one statement, or None when it was removed
    if visited is None:
        return []
    return visited if isinstance(visited, list) else [visited]

class Concretion(Enum):
    # how concrete compilation fills a model's magic values from the real result, by what the model returns
    VALUE = auto()      # `return x` of its one magic value: x is the real result
    VALUES = auto()     # `return (q, r)` of its magic values: the real result unpacked
    ONE_VALUE = auto()  # one magic() outside any loop, otherwise used (a branch, a reassigned variable...), or
                        # each in a branch that returns or raises after it: a run draws one value at most, the
                        # real result, and what the model returns must equal it
    ELEMENTS = auto()   # anything else: each magic() takes the next element of the real result, and what
                        # the model returns must equal the real result

# the concretions where magic() calls draw from an iterator made of the real result
DRAWING = {Concretion.ONE_VALUE, Concretion.ELEMENTS}

def concretion_of(node, magic_calls, magic_statements):
    names = [name for _, name in magic_statements]
    returned = [statement.value for statement in own_nodes(node) if isinstance(statement, ast.Return)]
    # the magic values can be plain variables only when each magic() is its own statement in the body, run
    # once, and nothing else binds its variable
    plain = len(magic_calls) == len(names) == len(set(names)) and not any(
        rebinds(node, name, statement) for statement, name in magic_statements
    )
    if plain and len(names) == 1 and all(isinstance(value, ast.Name) and value.id == names[0] for value in returned):
        return Concretion.VALUE
    if plain and len(names) > 1 and all(
        isinstance(value, (ast.Tuple, ast.List)) and [getattr(element, "id", None) for element in value.elts] == names
        for value in returned
    ):
        return Concretion.VALUES
    if len(magic_calls) == 1 and not in_loop(node, magic_calls[0]) or magic_calls and draws_at_most_once(node, magic_calls):
        return Concretion.ONE_VALUE
    return Concretion.ELEMENTS

class ModelCompiler(ast.NodeTransformer):
    # one pass over a model function's code, compiling each magic(), constraint(), return and raise it meets:
    # constraint(c) becomes `assert c` everywhere, flagged with a BrokenVariant for a model checker; concrete
    # compilation also rewrites the magic(), return and raise in the model's own scope for its concretion, leaving
    # nested functions and classes alone
    def __init__(self, func, name, compilation, concretion=None, result=None):
        self.func, self.name, self.compilation = func, name, compilation
        self.concretion, self.result = concretion, result
        self.constraints = []  # assertions to run right after the real call
        self.nested = 0

    def flag(self, *parts):
        # the BrokenVariant a constraint's assert fails with for a model checker; concretely, the values are the
        # real effect's, so a broken constraint is the model disagreeing with it, a plain assertion error
        if self.compilation is not Compilation.MODEL_CHECKER:
            return None
        return broken_variant(*parts, f": the model of {self.name} rejected this call")

    def calls(self, node, target):
        # a call of target by its name, an alias of it, or through its module (PyMagic.magic)
        if not isinstance(node, ast.Call):
            return False
        if isinstance(node.func, ast.Name) and node.func.id == target.__name__:
            return True
        return resolve(self.func, node.func) is target

    def is_magic(self, node):
        return self.calls(node, magic)

    def is_constraint(self, node):
        return self.calls(node, constraint)

    def in_scope(self):
        # whether concrete compilation rewrites the code being visited: the model's own, not nested code
        return self.compilation is Compilation.CONCRETE and not self.nested

    def push_constraint(self, assertions):
        self.constraints += assertions

    def compile(self, statements):
        return [new for statement in statements for new in as_list(self.visit(statement))]

    def visit_FunctionDef(self, definition):
        self.nested += 1
        self.generic_visit(definition)
        self.nested -= 1
        return definition
    visit_AsyncFunctionDef = visit_Lambda = visit_ClassDef = visit_FunctionDef

    def visit_Expr(self, statement):
        # constraint(c) becomes `assert c`
        if not self.is_constraint(statement.value):
            return self.generic_visit(statement)
        if len(statement.value.args) != 1 or statement.value.keywords:
            raise UncompilableModel(
                f"{self.func.__qualname__} calls {ast.unparse(statement.value)} (line {statement.lineno}); "
                f"constraint() takes exactly one condition"
            )
        condition = statement.value.args[0]
        return self.generic_visit(ast.Assert(condition, self.flag(f"the values broke {ast.unparse(condition)}")))

    def kind_check(self, name, kinds):
        # magic(kinds)'s check of its value, as an assert
        if not kinds:
            return []
        expected = " or ".join(map(ast.unparse, kinds))
        flag = self.flag(f"{name} = magic({', '.join(map(ast.unparse, kinds))}) drew ", ast.Name(name, ast.Load()),
                         f", which is not {expected}")
        return kind_assertion(name, kinds, flag)

    def visit_Assign(self, statement):
        # name = magic(kinds), which is magic() followed by constraint(isinstance(name, kinds))
        name = magic_variable(statement, self.is_magic)
        if name is None:
            return self.generic_visit(statement)
        kinds = statement.value.args
        if self.compilation is Compilation.MODEL_CHECKER:
            # the checker still sees the kinds in magic(kinds), and the assertion states them as a constraint
            return [self.generic_visit(statement), *self.kind_check(name, kinds)]
        if not self.in_scope():
            return self.generic_visit(statement)
        if self.concretion in DRAWING:
            return [ast.Assign([ast.Name(name, ast.Store())], next_magic()), *self.kind_check(name, kinds)]
        # the real result fills it at the start, so only its kind is left to assert
        self.push_constraint(self.kind_check(name, kinds))
        return None
    visit_AnnAssign = visit_Assign

    def visit_Call(self, call):
        # magic() and constraint() inside an expression
        self.generic_visit(call)
        if self.is_constraint(call):
            raise UncompilableModel(
                f"{self.func.__qualname__} uses {ast.unparse(call)} (line {call.lineno}) inside an expression; "
                f"write constraint() as its own statement so it compiles to an assertion"
            )
        if not self.is_magic(call):
            return call
        call.func = ast.Name(magic.__name__, ast.Load())
        if not self.in_scope():
            return call
        if call.args:
            raise UncompilableModel(
                f"{self.func.__qualname__} uses {ast.unparse(call)} (line {call.lineno}) inside an expression; "
                f"assign it to a variable first (`x = {ast.unparse(call)}`) so its kind can be asserted"
            )
        return next_magic()

    def visit_Return(self, statement):
        # drawing from the real result, what the model returns must equal it, and it is what the call returns
        self.generic_visit(statement)
        if not self.in_scope() or self.concretion not in DRAWING:
            return statement
        result = ast.Name("_result", ast.Load())
        value = statement.value or ast.Constant(None)
        return [ast.Assert(ast.Compare(value, [ast.Eq()], [result]), None), ast.Return(result)]

    def visit_Raise(self, statement):
        # the real effect has already returned, so a model reaching a raise disagrees with it
        self.generic_visit(statement)
        if not self.in_scope():
            return statement
        raised = ast.unparse(statement.exc) if statement.exc else "its exception again"
        return assertion_error(f"the real {self.name} returned ", self.result, f", but its model raises {raised} for it")

def compile_function(func, original, compilation):
    # (definition, helpers, real) of a model function compiled into a function named after its original:
    # constraint(c) becomes `assert c`, and magic() stays for a model checker, or takes its values from one call
    # to the original in concrete compilation, which real, (module, reference), says how to reach
    if func.__code__.co_freevars:
        raise UncompilableModel(
            f"{func.__qualname__} uses {list(func.__code__.co_freevars)} from the function around it, which the "
            f"compiled module can't see; move them to module level"
        )
    node = function_node(func)
    node.decorator_list = []
    node.name = original.__name__
    if compilation is Compilation.MODEL_CHECKER:
        node.body = ModelCompiler(func, node.name, compilation).compile(node.body)
        return node, set(), None
    return compile_concrete(func, original, node)

def compile_concrete(func, original, node):
    # rewrites node so the real effect runs first and fills the magic values: an exception it raises must be
    # one the model raises too, and once it has returned, the model's code checks its result; gives (node, the
    # helpers the compiled code needs, (module, reference) of the real effect)
    if differing := differing_default(func, original):
        mine, theirs = differing
        raise UncompilableModel(
            f"{func.__qualname__} gives {mine.name} {default_text(mine)}, but the real {name_of(original)} gives "
            f"{theirs.name} {default_text(theirs)}; the compiled function passes every argument on to the real one, "
            f"so give the model the same defaults"
        )
    module_name, reference = reference_to(original)
    # the real effect as the compiled module finds it on import, before the model is put in its place
    real_call = ast.Call(ast.Name(f"_real_{node.name}", ast.Load()), *passed_on(node.args))

    compiler = ModelCompiler(func, node.name, Compilation.CONCRETE)
    magic_calls = [call for call in ast.walk(node) if compiler.is_magic(call)]
    magic_statements = [(statement, magic_variable(statement, compiler.is_magic)) for statement in node.body]
    magic_statements = [(statement, name) for statement, name in magic_statements if name is not None]
    if not any(isinstance(statement, ast.Return) for statement in own_nodes(node)):
        raise UncompilableModel(
            f"{func.__qualname__} has no return to match against the result of the real {name_of(original)}"
        )
    used_names = {name for code in code_objects(func.__code__) for name in code.co_varnames + code.co_names}
    if taken := sorted(used_names & CONCRETE_NAMES):
        raise UncompilableModel(
            f"{func.__qualname__} uses {taken}, which the compiled code needs for the real result; rename them"
        )

    names = [name for _, name in magic_statements]
    compiler.concretion = concretion_of(node, magic_calls, magic_statements)
    if compiler.concretion is Concretion.VALUE:
        target, compiler.result = ast.Name(names[0], ast.Store()), ast.Name(names[0], ast.Load())
    else:
        target, compiler.result = ast.Name("_result", ast.Store()), ast.Name("_result", ast.Load())

    # a model reaching the end of its body returns None, which must match the real result too
    falls_off = not isinstance(node.body[-1], (ast.Return, ast.Raise))
    if falls_off and compiler.concretion in DRAWING:
        node.body.append(ast.Return(None))

    allowed = raised_types(node, func)  # before the pass rewrites the model's raises
    body = compiler.compile(node.body)
    prelude, helpers = real_call_statements(target, real_call, allowed, node.name), set()
    match compiler.concretion:
        case Concretion.VALUES:
            prelude += unpack_statements(names, node.name)
        case Concretion.ONE_VALUE:
            prelude += ast.parse("_magic = iter((_result,))").body
        case Concretion.ELEMENTS if magic_calls:
            prelude += ast.parse(f"_magic = _elements(_result, {node.name!r})").body
            helpers.add(ELEMENTS_HELPER)
    node.body = [*prelude, *compiler.constraints, *body]
    if falls_off and compiler.concretion not in DRAWING:
        node.body.append(assertion_error(f"the real {node.name} returned ", compiler.result, ", but its model returns None for it"))

    if any(compiler.is_magic(call) for call in ast.walk(node)):
        raise UncompilableModel(
            f"{func.__qualname__} calls magic() inside a nested function or class, which can't take its values "
            f"from the real {name_of(original)}"
        )
    return node, helpers, (module_name, reference)

# ------------------------------------------------------------------ C specs

# the C modules of CPython that Python's own semantics rest on, which every Python has: the interpreter's
# (builtins, sys, imports, marshal, I/O, threads, weak references, warnings, abstract classes, codecs), and those
# the pure modules of the standard library are written over (string formatting, regular expressions, itertools,
# and the cores of collections, functools and operator). A package compiled with no C specs needs no other C module
PYTHON_SEMANTICS = {
    "builtins", "sys", "_imp", "marshal", "_io", "_thread", "_weakref", "_warnings", "_abc", "_codecs", "_string",
    "_sre", "itertools", "_collections", "_functools", "_operator",
}

# what a try catches that lets a module go on without the C module it tries: CPython's way to a pure fallback
GOING_ON = {"ImportError", "ModuleNotFoundError", "Exception", "BaseException"}

def spec_of(name):
    # the spec of a module, found without running it or the packages above it; None where there's no such module
    module = sys.modules.get(name)
    if getattr(module, "__spec__", None) is not None:
        return module.__spec__
    above, _, _ = name.rpartition(".")
    if not above:
        try:
            return importlib.util.find_spec(name)
        except ValueError:
            return None
    package = spec_of(above)
    if package is None or package.submodule_search_locations is None:
        return None
    return importlib.machinery.PathFinder.find_spec(name, package.submodule_search_locations)

def is_c_spec(spec):
    # whether a module is written in C: built into the interpreter, or a shared library
    return spec.origin == "built-in" or isinstance(spec.loader, importlib.machinery.ExtensionFileLoader)

def with_packages(name):
    # a module and the packages above it, which importing it runs first
    parts = name.split(".")
    return [".".join(parts[:count]) for count in range(1, len(parts) + 1)]

def goes_on_without(statement):
    # whether a try goes on when what it imports isn't there: it has a handler for the ImportError that doesn't
    # just raise again
    for handler in statement.handlers:
        caught = {node.id for node in ast.walk(handler.type) if isinstance(node, ast.Name)} if handler.type else GOING_ON
        if caught & GOING_ON and not all(isinstance(line, ast.Raise) for line in handler.body):
            return True
    return False

def never_runs(test):
    # whether an if's test is false whenever a module is imported: it's run as the main program, or type-checked
    match test:
        case ast.Compare(left=ast.Name(id="__name__"), ops=[ast.Eq()], comparators=[ast.Constant(value="__main__")]):
            return True
        case ast.Name(id="TYPE_CHECKING") | ast.Attribute(attr="TYPE_CHECKING"):
            return True
    return False

def inner_statements(statement):
    # the statements a compound statement holds: its bodies, and its handlers' and cases'
    inner = [line for field in ("body", "orelse", "finalbody") for line in getattr(statement, field, [])
             if isinstance(line, ast.stmt)]
    return inner + [line for nested in getattr(statement, "handlers", []) + getattr(statement, "cases", [])
                    for line in nested.body]

def imported_by(statements, package, running, find=spec_of):
    # the modules statements import, with the packages above them; their relative imports are from package, or
    # left out without one. Running, those they may import as they run: not those of their functions or of their
    # ifs that never run on import, nor those of a try that goes on without them, but those of its handlers, else
    # and finally, which may run: a missing C module makes handlers run (hashlib without its C hashes imports
    # logging in one). find gives a module's spec, or None
    found = []
    for statement in statements:
        match statement:
            case ast.Import(names=aliases):
                found += [name for alias in aliases for name in with_packages(alias.name)]
            case ast.ImportFrom(module=module, names=aliases, level=level):
                try:
                    base = importlib.util.resolve_name("." * level + (module or ""), package) if level else module
                except (ImportError, ValueError):
                    continue  # relative, out of no package or beyond the top one, which fails as it runs
                found += with_packages(base)
                # what it takes from a package may be its submodules
                found += [f"{base}.{alias.name}" for alias in aliases if find(f"{base}.{alias.name}") is not None]
            case ast.FunctionDef() | ast.AsyncFunctionDef() if running:
                pass
            case ast.If(test=test, orelse=orelse) if running and never_runs(test):
                found += imported_by(orelse, package, running, find)
            case ast.Try() if running and goes_on_without(statement):
                handled = [line for handler in statement.handlers for line in handler.body]
                found += imported_by(handled + statement.orelse + statement.finalbody, package, running, find)
            case _:
                found += imported_by(inner_statements(statement), package, running, find)
    return found

def library_spec(name):
    # the spec of a frozen module's source in the library, as Python 3.11 freezes os, io and codecs; None where
    # there's none, as for the import system's own
    above, _, _ = name.rpartition(".")
    if not above:
        return importlib.machinery.PathFinder.find_spec(name)
    package = spec_of(above)
    if package is None or package.submodule_search_locations is None:
        return None
    return importlib.machinery.PathFinder.find_spec(name, package.submodule_search_locations)

def c_specs_needed(files):
    # the C modules a compiled package needs that aren't Python's own, each as the chain of imports that needs it:
    # from a compiled module, through the modules it imports and what they may import as they run, to the C module
    find = functools.lru_cache(maxsize=None)(spec_of)
    chains, waiting, needed = {}, deque(), []
    for path, source in sorted(files.items()):
        for name in imported_by(ast.parse(source).body, None, False, find):
            if name not in chains:
                chains[name] = [path, name]
                waiting.append(name)
    while waiting:
        name = waiting.popleft()
        spec = find(name)
        if spec is not None and spec.origin == "frozen":
            spec = library_spec(spec.name)
        if spec is None or spec.origin is None:
            continue
        if is_c_spec(spec):
            if name not in PYTHON_SEMANTICS:
                needed.append(chains[name])
            continue
        # under the name it has, which a module may not (os.path is posixpath)
        try:
            source = spec.loader.get_source(spec.name)
        except (ImportError, OSError):
            source = None
        if source is None:
            raise UncompilableModel(
                f"with no C specs, what the package imports is read from its source, and {name} has none, as "
                f"{chains[name][0]} imports {', which imports '.join(chains[name][1:])}"
            )
        package = spec.name if spec.submodule_search_locations is not None else spec.name.rpartition(".")[0]
        for found in imported_by(ast.parse(source).body, package, True, find):
            if found not in chains:
                chains[found] = chains[name] + [found]
                waiting.append(found)
    return needed

# ------------------------------------------------------------------ the package

# the module of the package holding what the models use of PyMagic itself, like BrokenVariant
PYMAGIC_MODULE = "_pymagic"

# the compiled package's __init__.py: the originals its modules stand in for. Nothing in it runs or imports, and
# nothing in the package puts a model in place: whoever runs the package does, as the tables say
PACKAGE_TABLES = '''\
# compiled from PyMagic models: the originals the compiled modules stand in for, which whoever runs the package
# puts the models in place of, as these tables say and nothing else:
# - a compiled module is named relative to this package, and stands in for the real module of its name
# - REPLACED has a row for every name a standard library module binds an original to, of those loaded while
#   compiling (the modules the models and their originals import), its own module's own name first; the rows of
#   a real module the package compiles name the model as they name the original
{placing}
# - a module in STANDING_IN is stood in for whole, and so is every package above it

# (real module, the original's name there, compiled module, the model's name there)
REPLACED = {replaced}

# (real module, compiled module)
STANDING_IN = {standing_in}
'''

# how the models of a package are put in place, for each compilation: in a Python without the C module a model
# checker's package compiles, its compiled module stands in for it whole; a concrete package needs the real modules
PLACING = {
    Compilation.MODEL_CHECKER: "# - where there is no C module the package compiles, its compiled module stands in for it whole",
    Compilation.CONCRETE: "# - the compiled modules call the real ones, which stay: each model goes in place of its original alone",
}

# the __init__.py of a package compiled to run in pure Python: comments alone, naming the real modules
PURE_INIT = '''\
# compiled from PyMagic models to run in pure Python, needing no C module but those Python's own semantics rest
# on. Nothing here runs: each compiled module stands in for the real module of its name, and holds the models of
# these real objects, under the names after the arrows
#
{lines}
'''

PACKAGE_INIT = "# compiled from PyMagic models: a package of compiled modules\n"

class Span:
    # a stretch of source, positioned as the nodes of a syntax tree are: lines from 1, columns in UTF-8 bytes
    def __init__(self, lineno, col_offset, end_lineno, end_col_offset):
        self.lineno, self.col_offset, self.end_lineno, self.end_col_offset = lineno, col_offset, end_lineno, end_col_offset

def rewritten(source, replacements):
    # source with each span or node of replacements, {span: text}, replaced by its text
    lines = source.splitlines(keepends=True)

    def column(line, offset):
        return len(lines[line - 1].encode("utf-8")[:offset].decode("utf-8"))
    edits = sorted(
        ((span.lineno, column(span.lineno, span.col_offset), span.end_lineno,
          column(span.end_lineno, span.end_col_offset), text) for span, text in replacements.items()),
        key=lambda edit: edit[:2], reverse=True,
    )
    for start_line, start, end_line, end, text in edits:
        lines[start_line - 1:end_line] = [lines[start_line - 1][:start] + text + lines[end_line - 1][end:]]
    return "".join(lines)

def bound_here(nodes):
    # the names bound directly in a scope's code: not in the functions, lambdas, classes and comprehensions in it,
    # whose names, decorators, defaults and bases are the scope's though; global ones aren't bound in it
    bound, declared, stack = set(), set(), list(nodes)
    while stack:
        node = stack.pop()
        match node:
            case ast.Name(id=name, ctx=ast.Store() | ast.Del()):
                bound.add(name)
            case ast.FunctionDef() | ast.AsyncFunctionDef() | ast.ClassDef():
                bound.add(node.name)
                stack += outside_of(node)
                continue
            case ast.Lambda() | ast.ListComp() | ast.SetComp() | ast.DictComp() | ast.GeneratorExp():
                stack += outside_of(node)
                continue
            case ast.alias(name=module, asname=alias):
                bound.add(alias or module.split(".")[0])
            case ast.ExceptHandler(name=name) if name:
                bound.add(name)
            case ast.Global(names=names):
                declared |= set(names)
        stack += ast.iter_child_nodes(node)
    return bound - declared

def outside_of(node):
    # the parts of a scope-making node evaluated in the scope around it
    match node:
        case ast.FunctionDef() | ast.AsyncFunctionDef():
            arguments = node.args
            return [*node.decorator_list, *arguments.defaults, *filter(None, arguments.kw_defaults), *filter(None, [
                node.returns, *(arg.annotation for arg in [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs,
                                                            arguments.vararg, arguments.kwarg] if arg)])]
        case ast.ClassDef():
            return [*node.decorator_list, *node.bases, *(keyword.value for keyword in node.keywords)]
        case ast.Lambda():
            return [*node.args.defaults, *filter(None, node.args.kw_defaults)]
        case ast.ListComp() | ast.SetComp() | ast.DictComp() | ast.GeneratorExp():
            return [node.generators[0].iter]
    return []

def argument_names(arguments):
    return {arg.arg for arg in [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs, arguments.vararg,
                                arguments.kwarg] if arg}

def global_references(tree):
    # the Name nodes of code that refer to module-level names, scoped as Python scopes them: a name bound in a
    # function, lambda or comprehension is local to it and what is nested in it; one bound in a class body is
    # local to the body only
    references = []

    def walk(node, scopes, class_bound):
        match node:
            case ast.AnnAssign() if scopes:
                # in a function, an annotated assignment doesn't evaluate its annotation
                for part in (node.target, node.value):
                    if part is not None:
                        walk(part, scopes, class_bound)
                return
            case ast.Name(id=name, ctx=ast.Load()):
                if name not in class_bound and not any(name in scope for scope in scopes):
                    references.append(node)
                return
            case ast.FunctionDef() | ast.AsyncFunctionDef():
                for part in outside_of(node):
                    walk(part, scopes, class_bound)
                inner = (*scopes, bound_here(node.body) | argument_names(node.args))
                for statement in node.body:
                    walk(statement, inner, set())
                return
            case ast.ClassDef():
                for part in outside_of(node):
                    walk(part, scopes, class_bound)
                body_bound = bound_here(node.body)
                for statement in node.body:
                    walk(statement, scopes, body_bound)
                return
            case ast.Lambda():
                for part in outside_of(node):
                    walk(part, scopes, class_bound)
                walk(node.body, (*scopes, bound_here([node.body]) | argument_names(node.args)), set())
                return
            case ast.ListComp() | ast.SetComp() | ast.DictComp() | ast.GeneratorExp():
                walk(node.generators[0].iter, scopes, class_bound)
                targets = bound_here([generator.target for generator in node.generators])
                inner = (*scopes, targets)
                elements = [node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]
                for part in [*elements, *(generator.target for generator in node.generators),
                             *(condition for generator in node.generators for condition in generator.ifs),
                             *(generator.iter for generator in node.generators[1:])]:
                    walk(part, inner, set())
                return
        for child in ast.iter_child_nodes(node):
            walk(child, scopes, class_bound)

    walk(tree, (), set())
    return references

def is_installed(module):
    return getattr(module, "__file__", "") and module.__file__.startswith(INSTALLED)

def is_project(module):
    # a module of this project: neither the standard library's nor an installed package's
    return module is not None and not is_standard(module.__name__) and not is_installed(module)

def module_file(name, packages):
    return "/".join(name.split(".")) + ("/__init__.py" if name in packages else ".py")

def relative_name(home, target, packages):
    # target as a module name relative to home's file in the package
    package = home if home in packages else home.rpartition(".")[0]
    return "." * (2 + package.count(".") if package else 1) + target

def model_decorators(tree, namespace):
    # the decorators of a definition that are PyMagic's model(): (the definition, the decorators)
    definition = tree.body[0]
    return [decorator for decorator in getattr(definition, "decorator_list", [])
            if isinstance(decorator, ast.Call) and resolved_in(namespace, decorator.func) is model]

def resolved_in(namespace, expression):
    # the object a name or dotted name refers to in a module's namespace, or None
    match expression:
        case ast.Name(id=name):
            return namespace.get(name, getattr(builtins, name, None))
        case ast.Attribute(value=owner, attr=attribute):
            owner = resolved_in(namespace, owner)
            return None if owner is None else getattr(owner, attribute, None)
    return None


class CompiledModule:
    # what goes into the compiled module standing in for one original module
    def __init__(self, name):
        self.name = name
        self.imports, self.helpers = set(), set()
        self.blocks = []    # the sources of its definitions, in the order they run
        self.names = {}     # local name: the object it names


class Package:
    # the compiled modules, one for each module the models stand in for: each model under its original's name,
    # and what the models use, written from its source, imported, or written as a literal; then __init__.py, which
    # says what they stand in for
    def __init__(self, compilation, no_c_spec=False, pure=False):
        self.compilation = compilation
        self.checker = compilation is Compilation.MODEL_CHECKER
        self.pure = pure                     # to run in pure Python: its __init__.py names the real modules alone
        self.no_c_spec = no_c_spec or pure   # needing no C module but those Python's semantics rest on
        self.modules = {}
        self.placed = {}     # id(object): (compiled module, its name there) for every model and what's written
        self.objects = {}    # id(object): object, to keep what placed refers to alive
        self.pending = {}    # id(object): how to write the model placed under it, until it's written
        self.written = set()
        self.replaced = []   # (original, its home, the model's name there) for each original the tables replace
        # the top-level packages of the models and of PyMagic: their code is written out, wherever it's installed
        self.model_packages = {__name__.split(".")[0]}
        self.modeled_by = {} # id(original): the model compiled for it
        self.modelled = set() # the modules the models stand in for

    def module(self, name):
        if name not in self.modules:
            self.modules[name] = CompiledModule(name)
        return self.modules[name]

    def place_model(self, original, user, *aliases):
        # an original's model, under the original's name in the module it belongs to; one model each
        home = home_of(original)
        if id(original) in self.placed:
            raise UncompilableModel(f"{self.modeled_by[id(original)]} and {user} both compile to {original.__name__} in {home}")
        self.modeled_by[id(original)] = user
        self.modelled.add(home)
        self.place(original, home, original.__name__, user, *aliases)
        return home

    def place(self, value, home, local, user, *aliases):
        module = self.module(home)
        if module.names.get(local, value) is not value:
            raise UncompilableModel(f"{user} and another model both compile to {local} in {home}")
        module.names[local] = value
        for placed in (value, *aliases):
            self.placed[id(placed)], self.objects[id(placed)] = (home, local), placed

    def free_name(self, home, preferred, value):
        # preferred, unless it names something else in home already
        names = self.module(home).names
        candidates = itertools.chain([preferred, f"_{preferred}"], (f"_{preferred}_{n}" for n in itertools.count(2)))
        return next(name for name in candidates if names.get(name, value) is value)

    # ---------------------------------------------------------------- the models

    def is_model_code(self, module):
        # whether module is code of the models, written into the package rather than imported: the models' own
        # packages and PyMagic, or a module of this project that isn't installed
        return module is not None and (module.__name__.split(".")[0] in self.model_packages or is_project(module))

    def add(self, kind, entry):
        # reserves where a model goes: under its original's name, in the module its original belongs to
        if kind is not Model.PATCHES:
            self.model_packages.add(defining_module(entry).__name__.split(".")[0])
        match kind:
            case Model.MODULE | Model.FUNCTION:
                members = [(unwrap(member), original) for _, member, original in model_members(entry) if original] \
                    if kind is Model.MODULE else [(unwrap(entry), entry.__model__)]
                for func, original in members:
                    home = self.place_model(original, func.__qualname__)
                    self.pending[id(original)] = lambda func=func, original=original: self.write_function(func, original)
                    self.replace(original, home, original.__name__)
            case Model.COPY:
                original = entry.__model__
                if not self.checker:
                    return  # concretely, the real effect raises and makes the real classes
                home = self.place_model(original, entry.__qualname__, entry)
                self.pending[id(original)] = lambda entry=entry: self.write_copy(entry)
                self.replace(original, home, original.__name__)
            case Model.CLASS:
                if not self.checker:
                    raise UncompilableModel(
                        f"{entry.__name__} models the class {name_of(entry.__model__)}, whose methods would get an "
                        f"instance of the compiled class rather than the real one; compile class models for a model "
                        f"checker"
                    )
                original = entry.__model__
                home = self.place_model(original, entry.__qualname__)
                self.pending[id(original)] = lambda entry=entry: self.write_class(entry)
                self.replace(original, home, original.__name__)
            case Model.PATCHES:
                raise UncompilableModel(
                    f"the plain patches of {[name_of(function) for function in entry]} have no model to compile; "
                    f"compile only @model classes and functions"
                )

    def replace(self, original, home, local):
        # an original the tables replace with its model: one the standard library holds, or, concretely, any
        if not self.checker or is_standard(home):
            self.replaced.append((original, home, local))

    def write_pending(self, value):
        if id(value) in self.pending:
            self.pending.pop(id(value))()

    def write_function(self, func, original):
        node, helpers, real = compile_function(func, original, self.compilation)
        home, local = self.placed[id(original)]
        module = self.module(home)
        module.helpers |= helpers
        if real is not None:
            module_name, reference = real
            module.imports.add(f"import {module_name}")
            captured = f"_real_{node.name}"
            self.place(object(), home, captured, func.__qualname__)
            module.blocks.append(f"{captured} = {reference}")
        node.name = local
        self.resolve_tree(node, func.__globals__, func.__module__, home, func.__qualname__)
        module.blocks.append(ast.unparse(ast.fix_missing_locations(node)))

    def write_class(self, entry):
        # a class model: a class named after the original, with its methods compiled
        original = entry.__model__
        home, local = self.placed[id(original)]
        methods = []
        for name, member, modelled in model_members(entry):
            func = unwrap(member)
            if modelled is not None:
                node = compile_function(func, modelled, self.compilation)[0]
            else:
                node = function_node(func)
            node.name = name if modelled is None else modelled.__name__
            self.resolve_tree(node, func.__globals__, func.__module__, home, func.__qualname__)
            methods.append(node)
        definition = ast.ClassDef(local, [], [], methods or [ast.Pass()], [])
        self.module(home).blocks.append(ast.unparse(ast.fix_missing_locations(definition)))

    def write_copy(self, entry):
        # a copy of a class, as its source has it, without its @model(); its @model methods are compiled as model
        # functions are
        original = entry.__model__
        home, local = self.placed[id(original)]
        methods = {name: (unwrap(member), modelled) for name, member, modelled in model_members(entry) if modelled}
        self.write_source(entry, home, local, entry.__qualname__, methods)

    # ---------------------------------------------------------------- what the models use

    def resolve_tree(self, node, namespace, owner, home, user):
        # rewrites a compiled definition's references to module-level names into what home calls them
        tree = ast.Module([node], [])
        replacements = self.references(tree, namespace, owner, home, user)
        renamed = {id(target): text for target, text in replacements.items()}

        class Renaming(ast.NodeTransformer):
            def visit(self, child):
                if id(child) in renamed:
                    return ast.copy_location(ast.Name(renamed[id(child)], ast.Load()), child)
                return super().visit(child)
        Renaming().visit(tree)

    def references(self, tree, namespace, owner, home, user, dropped=()):
        # {node: name in home} for the references to module-level names in tree that home calls otherwise, but
        # those in the dropped nodes
        parents = {id(child): parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        skipped = {id(node) for drop in dropped for node in ast.walk(drop)}
        replacements = {}
        # the flags the compiler writes name PyMagic's BrokenVariant, wherever the model is
        given = {"BrokenVariant": BrokenVariant} if self.checker else {}
        for name_node in global_references(tree):
            if id(name_node) in skipped:
                continue
            if name_node.id not in namespace and name_node.id not in given:
                continue  # a builtin, or a name the compiler gives: magic, _result...
            target, label = name_node, name_node.id
            value = namespace.get(label, given.get(label))
            # a module's attribute that is a model, or anything of a project module, is referred to directly; a
            # submodule is followed, to import it; so is a value of a module the models stand in for, written out
            while isinstance(value, types.ModuleType) and isinstance(parents.get(id(target)), ast.Attribute):
                attribute = parents[id(target)].attr
                held = getattr(value, attribute, None)
                if not (id(held) in self.placed or self.is_model_code(value) or isinstance(held, types.ModuleType)
                        or self.checker and value.__name__ in self.modelled and is_value(held)
                        or self.checker and id(held) in named_only
                        or self.checker and is_system(value.__name__) and is_constant(held)):
                    break
                target, value, label = parents[id(target)], held, attribute
            if isinstance(value, types.ModuleType) and not self.is_model_code(value) and ast.unparse(target) == value.__name__:
                used = ast.unparse(parents.get(id(target), target))
                if self.checker and value.__name__ in self.modelled:
                    raise UncompilableModel(
                        f"{user} uses {used} of {value.__name__}, which the models stand in for but don't model"
                    )
                self.check_runnable(value.__name__, user, used)
                self.module(home).imports.add(f"import {value.__name__}")  # `urllib.parse.urlsplit` stays as it is
                continue
            # a copy's original is the copy itself where there are no originals: in a module for a model checker
            parent = parents.get(id(target))
            if self.checker and isinstance(parent, ast.Attribute) and parent.attr == "__model__" and \
                    getattr(value, "__model_kind__", None) is Model.COPY and id(value) in self.placed:
                target = parent
            local = self.local_name(home, label, value, user, owner)
            if local != ast.unparse(target):
                replacements[target] = local
        return replacements

    def local_name(self, home, preferred, value, user, owner):
        # what home calls value, writing or importing it there as needed; owner is the module whose code names it
        module = self.module(home)
        if value is magic or value is constraint:
            return preferred
        if preferred == "BrokenVariant" and value is not BrokenVariant and self.checker:
            raise UncompilableModel(
                f"{user} uses a BrokenVariant of its own, but the compiled module defines BrokenVariant as PyMagic's; "
                f"rename it"
            )
        if self.checker and is_constant(value):
            # a value that can't change is written where it's used, under the name it has there: the same number
            # may be another constant elsewhere
            local = self.free_name(home, preferred, value)
            if local not in module.names:
                module.names[local] = value
                module.blocks.append(f"{local} = {value!r}")
            return local
        if id(value) in self.placed:
            self.write_pending(value)
            placed_home, placed_name = self.placed[id(value)]
            if placed_home == home:
                return placed_name
            return self.imported(home, preferred, value, relative_name(home, placed_home, self.packages()), placed_name)
        if isinstance(value, types.ModuleType):
            if self.is_model_code(value):
                raise UncompilableModel(f"{user} uses the module {value.__name__} as a whole, which isn't compiled")
            self.check_runnable(value.__name__, user, preferred)
            return self.imported(home, preferred, value, None, value.__name__)
        if is_literal(value) and self.checker:
            # one that can change is one object, which every module refers to where it's written first
            local = self.free_name(home, preferred, value)
            self.place(value, home, local, user)
            module.blocks.append(f"{local} = {value!r}")
            return local
        if self.checker and id(value) in named_only:
            found = standard_home(value, preferred) or (value.__module__, value.__name__)
            return self.stand_in(home, preferred, value, user, *found)
        if not is_literal(value) and (found := standard_home(value, preferred)):
            if self.checker and found[0] in self.modelled:
                if isinstance(value, Enum):
                    # a member: its enumeration's, by name, or by value when it's a combination of flags
                    kind = self.local_name(home, type(value).__name__, type(value), user, owner)
                    return f"{kind}.{value.name}" if value.name in type(value).__members__ else f"{kind}({int(value)!r})"
                if isinstance(value, type) and issubclass(value, Enum):
                    return self.enumeration(home, preferred, value, user, *found)
                return self.stand_in(home, preferred, value, user, *found)
            self.check_runnable(found[0], user, preferred)
            return self.imported(home, preferred, value, *found)
        defined_in = sys.modules.get(getattr(value, "__module__", None))
        if is_installed(defined_in) and not self.is_model_code(defined_in):
            return self.imported(home, preferred, value, defined_in.__name__, value.__name__)
        if not self.checker:
            return self.imported(home, preferred, value, owner, preferred)
        return self.written_out(home, preferred, value, user, owner)

    def check_runnable(self, module_name, user, used):
        # a module compiled for a model checker imports nothing of the process or the system
        if self.checker and is_system(module_name):
            raise UncompilableModel(
                f"{user} uses {used} of {module_name}, which a model checker can't run; keep what the model needs of "
                f"it in a module-level constant, which is written out as a literal, or model it"
            )

    def imported(self, home, preferred, value, module_name, attribute):
        # value imported into home, as attribute of module_name, or as the module itself without one
        local = self.free_name(home, preferred, value)
        self.module(home).names[local] = value
        if module_name is None:
            line = f"import {attribute}" + ("" if attribute == local else f" as {local}")
        else:
            line = f"from {module_name} import {attribute}" + ("" if attribute == local else f" as {local}")
        self.module(home).imports.add(line)
        return local

    def enumeration(self, home, preferred, value, user, module_name, attribute):
        # an enumeration of a module the models stand in for, written from its members into its compiled module:
        # its values are numbers, which a model checker needs as they are
        if id(value) not in self.placed:
            self.place(value, module_name, attribute, user)
            base = next(kind for kind in value.__mro__[1:] if kind.__module__ == "enum")
            base_name = self.local_name(module_name, base.__name__, base, user, module_name)
            members = "".join(f"\n    {name} = {member.value!r}" for name, member in value.__members__.items())
            self.module(module_name).blocks.append(f"class {attribute}({base_name}):" + (members or "\n    pass"))
        return self.local_name(home, preferred, value, user, module_name)

    def stand_in(self, home, preferred, value, user, module_name, attribute):
        # what a model names of a module the models stand in for, but doesn't model: an empty class or an object
        # in its compiled module, as no model makes one, nor needs more than its name; or an exception class the
        # models raise as it is, with the original's bases, which stands in for it in the tables too, so that the
        # program catches what the models raise
        if not isinstance(value, type) and callable(value):
            raise UncompilableModel(
                f"{user} calls {module_name}.{attribute}, which the models stand in for but don't model"
            )
        if id(value) not in self.placed:
            self.place(value, module_name, attribute, user)
            if isinstance(value, type) and issubclass(value, BaseException):
                bases = ", ".join(base.__name__ if getattr(builtins, base.__name__, None) is base
                                  else self.local_name(module_name, base.__name__, base, user, module_name)
                                  for base in value.__bases__)
                block = (f"class {attribute}({bases}):\n    # stands in for {module_name}.{attribute}, which the "
                         f"models raise as it is\n    __module__ = {module_name!r}")
                self.replace(value, module_name, attribute)
            elif isinstance(value, type):
                block = f"class {attribute}:\n    # named only: no model makes one\n    pass"
            else:
                block = f"{attribute} = object()  # named only: the models compare against it"
            self.module(module_name).blocks.append(block)
        return self.local_name(home, preferred, value, user, module_name)

    def written_out(self, home, preferred, value, user, owner):
        # a function, class or module-level value of the project, written into home from its source; PyMagic's
        # own go into the package's _pymagic module
        if isinstance(value, (type, types.FunctionType)) and self.is_model_code(defining_module(value)):
            where = PYMAGIC_MODULE if defining_module(value).__name__.split(".")[0] == __name__.split(".")[0] else home
            local = self.free_name(where, value.__name__, value)
            self.place(value, where, local, user)
            self.write_source(value, where, local, user)
            return local if where == home else self.local_name(home, preferred, value, user, owner)
        found = assignment_of(value, owner, self.is_model_code)
        if found is None:
            raise UncompilableModel(
                f"{user} uses {preferred}, {value!r}, which the compiled module can neither import nor write out"
            )
        module_name, statement, source, bound = found
        locals_ = {}
        for name in bound:
            locals_[name] = self.free_name(home, name, vars(sys.modules[module_name])[name])
            self.place(vars(sys.modules[module_name])[name], home, locals_[name], user)
        tree = ast.parse(source)
        replacements = self.references(tree, vars(sys.modules[module_name]), module_name, home, user)
        for target in [node for node in ast.walk(tree) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)]:
            if locals_.get(target.id, target.id) != target.id:
                replacements[target] = locals_[target.id]
        self.module(home).blocks.append(rewritten(source, replacements).rstrip("\n"))
        return self.placed[id(value)][1]

    def write_source(self, value, home, local, user, methods=None):
        # a function or class as its source has it, without @model(), with its references renamed for home; the
        # methods of a class named in methods, {name: (function, original)}, compiled as model functions are
        module = defining_module(value)
        source = source_of(value, module)
        if source is None:
            raise UncompilableModel(f"{user} uses {value.__name__}, whose source isn't available") from None
        tree = ast.parse(source)
        namespace = vars(module)
        decorators = model_decorators(tree, namespace)
        compiled = [node for node in getattr(tree.body[0], "body", [])
                    if isinstance(node, ast.FunctionDef) and node.name in (methods or {})]
        replacements = self.references(tree, namespace, module.__name__, home, user, dropped=decorators + compiled)
        lines = source.splitlines(keepends=True)
        for decorator in decorators:
            end = len(lines[decorator.end_lineno - 1].encode("utf-8"))
            replacements[Span(decorator.lineno, 0, decorator.end_lineno, end)] = ""
        for method in compiled:
            func, original = methods[method.name]
            node = compile_function(func, original, self.compilation)[0]
            node.name = method.name
            self.resolve_tree(node, func.__globals__, func.__module__, home, f"{user}.{method.name}")
            first = min([method.lineno] + [decorator.lineno for decorator in method.decorator_list])
            end = len(lines[method.end_lineno - 1].encode("utf-8"))
            text = textwrap.indent(ast.unparse(ast.fix_missing_locations(node)), " " * method.col_offset) + "\n"
            replacements[Span(first, 0, method.end_lineno, end)] = text
        definition = tree.body[0]
        if local != definition.name:
            line = lines[definition.lineno - 1]
            keyword = "class" if isinstance(definition, ast.ClassDef) else "def"
            at = line.index(f"{keyword} {definition.name}") + len(keyword) + 1
            start = len(line[:at].encode("utf-8"))
            replacements[Span(definition.lineno, start, definition.lineno, start + len(definition.name.encode()))] = local
        self.module(home).blocks.append(rewritten(source, replacements).rstrip("\n"))

    # ---------------------------------------------------------------- the files

    def packages(self):
        return {name.rsplit(".", depth)[0] for name in self.modules for depth in range(1, name.count(".") + 1)}

    def standing_in(self):
        # the modules the compiled ones stand in for as a whole: those the models stand in for that aren't the
        # standard library's, for a model checker, and the packages above them
        if not self.checker:
            return []
        whole = {above for name in self.modelled if not is_standard(name) for above in with_packages(name)}
        return [(name, name) for name in sorted(whole)]

    def reexport(self):
        # a module standing in for another keeps the names it has for what's compiled in other modules
        for name in sorted(name for name, _ in self.standing_in() if name in self.modelled):
            original = sys.modules.get(name)
            for attribute, value in sorted(vars(original).items()) if original else []:
                if attribute.startswith("__") or attribute in self.modules[name].names:
                    continue
                if id(value) in self.placed and self.placed[id(value)][0] != name:
                    self.local_name(name, attribute, value, name, name)

    def write_constants(self):
        # a compiled module of a C module has its constants too, as the C module has them, so that it can stand in
        # for the whole module in a Python without it
        if not self.checker:
            return
        for name, module in sorted(self.modules.items()):
            spec = spec_of(name) if is_standard(name) and name != "builtins" else None
            if spec is None or not is_c_spec(spec):
                continue
            constants = [(key, value) for key, value in vars(importlib.import_module(name)).items()
                         if not key.startswith("__") and key not in module.names and is_constant(value)]
            for key, value in constants:
                module.names[key] = value
            if constants:
                module.blocks.append(f"# the constants of {name}\n" + "\n".join(f"{key} = {value!r}" for key, value in constants))

    def rows(self):
        # REPLACED: for each original, the row of its own name in its home, then one for every other name a
        # standard library module binds it to, of those loaded as it compiles, which are the modules the models and
        # their originals import; a module the package compiles gets its other names for it too, so that its rows
        # name the model as they name the original
        holders = {}
        for holder, module in sorted(list(sys.modules.items())):
            if module is not None and is_standard(holder) and holder != "__main__":
                for key, value in list(vars(module).items()):
                    holders.setdefault(id(value), []).append((holder, key))
        rows = []
        for original, home, local in self.replaced:
            names = [key for key, value in vars(sys.modules[home]).items() if value is original]
            # its own name there, else another it has there, else its own when something else stands there now,
            # as in a test's patch
            own = original.__name__ if original.__name__ in names or not names else names[0]
            rows.append((home, own, home, local))
            compiled = self.module(home)
            for holder, key in holders.get(id(original), []):
                if (holder, key) == (home, own):
                    continue
                if holder == home and compiled.names.get(key, original) is original:
                    if key not in compiled.names:
                        compiled.names[key] = original
                        compiled.blocks.append(f"{key} = {local}")
                    rows.append((home, key, home, key))
                else:
                    rows.append((holder, key, home, local))
        return rows

    def files(self):
        while self.pending:
            self.write_pending(self.objects[next(iter(self.pending))])
        self.reexport()
        self.write_constants()
        relative = lambda name: "." + name
        rows = [(module, name, relative(home), local) for module, name, home, local in self.rows()]
        standing_in = [(module, relative(home)) for module, home in self.standing_in()]
        packages = self.packages()
        header = COMPILED_HEADERS[self.compilation]
        files = {}
        for name, module in sorted(self.modules.items()):
            parts = [header, *sorted(module.imports), *("\n\n" + block for block in [*sorted(module.helpers), *module.blocks])]
            files[module_file(name, packages)] = "\n".join(parts) + "\n"
        for name in sorted(packages - set(self.modules)):
            files[module_file(name, packages)] = PACKAGE_INIT
        if self.pure:
            lines = [f"#   {module}.{name} -> {home}.{local}" for module, name, home, local in rows]
            lines += [f"#   {module} -> {home}, whole" for module, home in standing_in]
            files["__init__.py"] = PURE_INIT.format(lines="\n".join(lines) or "#   (nothing)")
            for path, source in files.items():
                try:
                    compile(source, path, "exec")
                except SyntaxError as error:
                    raise UncompilableModel(f"the compiled {path} isn't Python: {error}") from None
        else:
            files["__init__.py"] = PACKAGE_TABLES.format(
                placing=PLACING[self.compilation], replaced=pretty(rows), standing_in=pretty(standing_in))
        if self.no_c_spec and (needed := c_specs_needed(files)):
            raise UncompilableModel(
                "with no C specs, the package can need no C module but those Python's own semantics rest on, and it "
                "needs " + "; ".join(f"{chain[-1]}, as {chain[0]} imports {', which imports '.join(chain[1:])}"
                                     for chain in needed)
                + ". Model what the models need of them, or reach it through models"
            )
        return files

def defining_module(value):
    # the module that defines a function or class: the one it names, unless that doesn't hold it (a copy may name
    # its original's module), then one of the project's that does
    named = sys.modules.get(getattr(value, "__module__", None))
    qualname = getattr(value, "__qualname__", "").split(".")
    def holds(module):
        found = module
        for part in qualname:
            found = getattr(found, part, None)
        return found is value
    if named is not None and holds(named):
        return named
    return next((module for module in list(sys.modules.values())
                 if module is not None and not is_standard(module.__name__) and holds(module)), named)

def source_of(value, module):
    # the source of a function or class in module, decorators included and dedented, found by its qualified name
    try:
        text = inspect.getsource(module)
    except (OSError, TypeError):
        return None
    scope = ast.parse(text)
    for part in value.__qualname__.split("."):
        scope = next((node for node in getattr(scope, "body", []) if isinstance(node, (ast.ClassDef, ast.FunctionDef,
                      ast.AsyncFunctionDef)) and node.name == part), None)
        if scope is None:
            return None
    lines = text.splitlines(keepends=True)
    first = min([scope.lineno, *(decorator.lineno for decorator in scope.decorator_list)])
    return textwrap.dedent("".join(lines[first - 1:scope.end_lineno]))

def pretty(rows):
    return "[\n" + "".join(f"    {row!r},\n" for row in rows) + "]" if rows else "[]"

def assignment_of(value, owner, is_model_code):
    # (module, statement, its source, the names it binds) of the module-level assignment that makes value, in the
    # module whose code names it or another of the models' code
    candidates = [sys.modules.get(owner)] + [module for module in list(sys.modules.values()) if is_model_code(module)]
    for module in candidates:
        if not is_model_code(module):
            continue
        names = {name for name, held in vars(module).items() if held is value}
        if not names:
            continue
        try:
            source = inspect.getsource(module)
        except (OSError, TypeError):
            continue
        for statement in reversed(ast.parse(source).body):
            if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                bound = bound_here([statement])
                if bound & names:
                    return module.__name__, statement, ast.get_source_segment(source, statement), bound
    return None

def compile_models(models, compilation, *, no_c_spec=False, pure=False):
    # the package the models compile to, {path: source}: a module for each module the models stand in for, with
    # each model under its original's name, and an __init__.py with the tables of what they stand in for. With no
    # C specs, it may need no C module of the standard library but those Python's semantics rest on; pure, it also
    # runs in pure Python, and its __init__.py names the real modules in comments alone
    package = Package(compilation, no_c_spec, pure)
    for kind, entry in get_models(models):
        package.add(kind, entry)
    return package.files()

def write_package(files, directory):
    # writes a compiled package's files under directory
    import pathlib
    for path, source in files.items():
        target = pathlib.Path(directory, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source)
