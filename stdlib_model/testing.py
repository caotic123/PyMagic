# what the tests of the standard library models share: running calls on the models, and their outcomes

from PyMagic import Model, run_model

from . import models
from .magics import stdlib_magics

NOW = 1_700_000_000


def outcome(call):
    # what a call returned, or the type and message of what it raised, a copy's type as the original it copies
    try:
        return "returned", call()
    except Exception as error:
        kind = type(error)
        if getattr(kind, "__model_kind__", None) is Model.COPY:
            kind = kind.__model__
        return "raised", kind, str(error)

def modelled(call, magics=None):
    # the outcome of a call run on the standard library models, at NOW unless other magics are given
    return outcome(lambda: run_model(models, stdlib_magics(now=NOW) if magics is None else magics)(call))

def modelled_each(calls, magics=None):
    # the outcome of each call, all run on the standard library models at once
    return modelled(lambda: [outcome(call) for call in calls], magics)[1]

def each(calls):
    return [outcome(call) for call in calls]


# ------------------------------------------------------------------ what the models may use of the libraries

import ast
import importlib

from PyMagic import get_models

# the modules the standard library models model, which the models reach only through them
MODELLED_MODULES = {"binascii", "hashlib", "_hashlib", "zlib", "time", "random", "unicodedata", "encodings", "http",
                    "socket", "_socket", "re", "errno", "email"}

# what the models name without running it: an identity or a class for isinstance(), which no model makes
NAMED_ONLY = {"http.client.HTTPResponse", "email.message.Message", "socket._GLOBAL_DEFAULT_TIMEOUT"}

def library_uses(path, libraries=MODELLED_MODULES):
    # (line, dotted name) of each use a model file makes of the libraries' modules outside @model() decorators
    # and annotations that aren't evaluated
    tree = ast.parse(path.read_text())
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    in_decorators = {id(node) for definition in ast.walk(tree) if isinstance(definition, (ast.FunctionDef, ast.ClassDef))
                     for decorator in definition.decorator_list for node in ast.walk(decorator)}
    # an annotated assignment in a function doesn't evaluate its annotation
    in_decorators |= {id(node) for function in ast.walk(tree) if isinstance(function, ast.FunctionDef)
                      for assignment in ast.walk(function) if isinstance(assignment, ast.AnnAssign)
                      for node in ast.walk(assignment.annotation)}
    # the names imported libraries are bound to: their own, or what they're imported as
    bound = {alias.asname or alias.name.split(".")[0]: alias.name if alias.asname else alias.name.split(".")[0]
             for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
             if alias.name.split(".")[0] in libraries}
    uses = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module.split(".")[0] in libraries:
            uses += [(node.lineno, f"{node.module}.{alias.name}") for alias in node.names]
        if isinstance(node, ast.Name) and node.id in bound and isinstance(node.ctx, ast.Load) and \
                id(node) not in in_decorators:
            dotted = node
            while isinstance(parents.get(dotted), ast.Attribute):
                dotted = parents[dotted]
            uses.append((node.lineno, bound[node.id] + ast.unparse(dotted)[len(node.id):]))
    return uses

def resolved(dotted):
    # the object a dotted name names, importing what it needs
    parts = dotted.split(".")
    for split in range(len(parts), 0, -1):
        try:
            found = importlib.import_module(".".join(parts[:split]))
        except ImportError:
            continue
        for part in parts[split:]:
            found = getattr(found, part)
        return found
    raise ImportError(dotted)

def modelled_originals(all_models):
    # what the models stand in for: the functions and classes they model
    from PyMagic.model import model_members
    originals = set()
    for kind, entry in get_models(all_models):
        if kind is Model.COPY:
            originals.add(entry.__model__)
        originals |= {original for _, _, original in model_members(entry) if original is not None}
    return originals

def improper_uses(path, all_models):
    # the uses of the libraries a model file makes that are neither the models, by their originals' names, names
    # only, nor values, which compile as they are: numbers and the like, and enumerations
    from PyMagic.compile import is_value
    originals = modelled_originals(all_models)
    return [(line, dotted) for line, dotted in library_uses(path)
            if dotted not in NAMED_ONLY and not is_value(resolved(dotted)) and resolved(dotted) not in originals]
