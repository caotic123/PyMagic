# compile_models and its helpers: writing models out as Python source, for a model checker or with the
# real effects in place of magic()

import ast
import sys
import types
import builtins
from enum import Enum, auto
from inspect import Parameter, signature, isclass, unwrap, findsource

from .model import Model, code_objects, constraint, get_models, magic, model_members, name_of, signature_of


class UncompilableModel(BaseException):
    pass


class Compilation(Enum):
    MODEL_CHECKER = auto()  # magic() stays undefined, for the model checker to supply
    CONCRETE = auto()       # magic() takes its values from the real effect the model stands in for

COMPILED_HEADERS = {
    Compilation.MODEL_CHECKER: "# compiled from MagicF models for a model checker, which supplies magic()",
    Compilation.CONCRETE: "# compiled from MagicF models, with the real effects in place of magic()",
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

def global_imports(func, node):
    # import lines for the module-level names the compiled function uses, in its body, defaults and annotations
    local = {name for code in code_objects(func.__code__) for name in code.co_varnames + code.co_cellvars}
    imports = set()
    for name in {child.id for child in ast.walk(node) if isinstance(child, ast.Name)} - local:
        if name not in func.__globals__:
            continue
        value = func.__globals__[name]
        if value is magic or value is constraint:
            continue
        if isinstance(value, types.ModuleType):
            imports.add(f"import {value.__name__}" if value.__name__ == name else f"import {value.__name__} as {name}")
        else:
            imports.add(f"from {func.__module__} import {name}")
    return imports

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

def kind_assertion(name, kinds):
    # `assert isinstance(name, kind)` for a magic variable drawn with kinds, a tuple of them for several
    if not kinds:
        return []
    kind = kinds[0] if len(kinds) == 1 else ast.Tuple(kinds, ast.Load())
    return [ast.Assert(ast.Call(ast.Name("isinstance", ast.Load()), [ast.Name(name, ast.Load()), kind], []), None)]

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
    ONE_VALUE = auto()  # one magic() outside any loop, otherwise used (a branch, a reassigned variable...):
                        # the real result is the value it draws, and what the model returns must equal it
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
    if len(magic_calls) == 1 and not in_loop(node, magic_calls[0]):
        return Concretion.ONE_VALUE
    return Concretion.ELEMENTS

class ModelCompiler(ast.NodeTransformer):
    # one pass over a model function's code, compiling each magic(), constraint(), return and raise it meets:
    # constraint(c) becomes `assert c` everywhere; concrete compilation also rewrites the magic(), return and
    # raise in the model's own scope for its concretion, leaving nested functions and classes alone
    def __init__(self, func, name, compilation, concretion=None, result=None):
        self.func, self.name, self.compilation = func, name, compilation
        self.concretion, self.result = concretion, result
        self.constraints = []  # assertions to run right after the real call
        self.nested = 0

    def calls(self, node, target):
        # a call of target by its name, an alias of it, or through its module (MagicF.magic)
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
        return self.generic_visit(ast.Assert(statement.value.args[0], None))

    def visit_Assign(self, statement):
        # name = magic(kinds), which is magic() followed by constraint(isinstance(name, kinds))
        name = magic_variable(statement, self.is_magic)
        if name is None:
            return self.generic_visit(statement)
        kinds = statement.value.args
        if self.compilation is Compilation.MODEL_CHECKER:
            # the checker still sees the kinds in magic(kinds), and the assertion states them as a constraint
            return [self.generic_visit(statement), *kind_assertion(name, kinds)]
        if not self.in_scope():
            return self.generic_visit(statement)
        if self.concretion in DRAWING:
            return [ast.Assign([ast.Name(name, ast.Store())], next_magic()), *kind_assertion(name, kinds)]
        # the real result fills it at the start, so only its kind is left to assert
        self.push_constraint(kind_assertion(name, kinds))
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
    # (definition, imports, helpers) of a model function compiled into a function named after its original:
    # constraint(c) becomes `assert c`, and magic() stays for a model checker, or takes its values from
    # one call to the original in concrete compilation
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
        imports, helpers = set(), set()
    else:
        imports, helpers = compile_concrete(func, original, node)
    return node, imports | global_imports(func, node), helpers

def compile_concrete(func, original, node):
    # rewrites node so the real effect runs first and fills the magic values: an exception it raises must be
    # one the model raises too, and once it has returned, the model's code checks its result; gives the
    # (imports, helpers) the compiled code needs
    if differing := differing_default(func, original):
        mine, theirs = differing
        raise UncompilableModel(
            f"{func.__qualname__} gives {mine.name} {default_text(mine)}, but the real {name_of(original)} gives "
            f"{theirs.name} {default_text(theirs)}; the compiled function passes every argument on to the real one, "
            f"so give the model the same defaults"
        )
    module_name, reference = reference_to(original)
    real_call = ast.Call(ast.parse(reference, mode="eval").body, *passed_on(node.args))

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
    return {f"import {module_name}"}, helpers

def compile_models(models, compilation):
    # Python source of a module with one function per model function, named after the original it stands in for
    imports, helpers, functions, compiled_from = set(), set(), [], {}
    for kind, entry in get_models(models):
        match kind:
            case Model.MODULE:
                members = [(unwrap(member), original) for _, member, original in model_members(entry) if original]
            case Model.FUNCTION:
                members = [(unwrap(entry), entry.__model__)]
            case Model.CLASS:
                raise UncompilableModel(
                    f"{entry.__name__} models the class {name_of(entry.__model__)}; compiling class models isn't "
                    f"supported yet, only function models"
                )
            case Model.COPY:
                raise UncompilableModel(
                    f"{entry.__name__} is a copy of {name_of(entry.__model__)}; compiling copies of classes isn't "
                    f"supported yet, only function models"
                )
            case Model.PATCHES:
                raise UncompilableModel(
                    f"the plain patches of {[name_of(function) for function in entry]} have no model to compile; "
                    f"compile only @model classes and functions"
                )
        for func, original in members:
            node, needed, needed_helpers = compile_function(func, original, compilation)
            if node.name in compiled_from:
                raise UncompilableModel(
                    f"{compiled_from[node.name]} and {func.__qualname__} both compile to a function named {node.name}"
                )
            compiled_from[node.name] = func.__qualname__
            imports |= needed
            helpers |= needed_helpers
            functions.append(ast.unparse(ast.fix_missing_locations(node)))
    return "\n".join(
        [COMPILED_HEADERS[compilation], *sorted(imports), *("\n\n" + f for f in [*sorted(helpers), *functions])]
    ) + "\n"
