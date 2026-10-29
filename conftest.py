# what the tests share: running a compiled package in a Python of its own, as a model checker runs it

import subprocess
import sys

import pytest

from PyMagic.compile import PYTHON_SEMANTICS, write_package

# what a checker does with a compiled package before the program runs, done here in CPython: it puts the models in
# place as the tables of the package's __init__.py say, read as literals, and as nothing else does. A C module the
# package compiles that this Python hasn't loaded is the compiled module, whole, unless the package is concrete;
# the rows of a module loaded already are set now; those of any other are set once it has run, as lines appended
# to its source would set them; a module stood in for whole is the compiled one
PLACING = '''\
import sys, ast, importlib, importlib.abc, importlib.machinery, importlib.util

def _place(package, whole):
    tables = {}
    for statement in ast.parse(open(importlib.util.find_spec(package).origin).read()).body:
        if isinstance(statement, ast.Assign):
            tables[statement.targets[0].id] = ast.literal_eval(statement.value)
    compiled = lambda name: importlib.import_module(name, package)
    later = {}
    for module, name, home, local in tables["REPLACED"]:
        spec = None if module in sys.modules else importlib.util.find_spec(module)
        written_in_c = spec is not None and (
            spec.origin == "built-in" or isinstance(spec.loader, importlib.machinery.ExtensionFileLoader))
        if whole and written_in_c and home == "." + module:
            sys.modules[module] = compiled(home)
        elif module in sys.modules:
            setattr(sys.modules[module], name, getattr(compiled(home), local))
        else:
            later.setdefault(module, []).append((name, home, local))
    for module, home in tables["STANDING_IN"]:
        sys.modules[module] = compiled(home)

    class Placing(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path=None, target=None):
            spec = importlib.machinery.PathFinder.find_spec(name, path) if name in later else None
            if spec is not None:
                run = spec.loader.exec_module
                def exec_module(module):
                    run(module)
                    for key, home, local in later[name]:
                        setattr(module, key, getattr(compiled(home), local))
                spec.loader.exec_module = exec_module
            return spec
    sys.meta_path.insert(0, Placing())

_place("compiled", whole=WHOLE)
'''

# a pure Python, made of CPython: it has no C module but those Python's own semantics rest on, apart from those it
# loaded to start
PURE = '''\
import sys, importlib.abc, importlib.machinery

class _Pure(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        spec = importlib.machinery.BuiltinImporter.find_spec(name) or importlib.machinery.PathFinder.find_spec(name, path)
        written_in_c = spec is not None and (
            spec.origin == "built-in" or isinstance(spec.loader, importlib.machinery.ExtensionFileLoader))
        if written_in_c and name not in SEMANTICS:
            raise ImportError(f"{name} is written in C, and this Python has no C modules")
        return None
sys.meta_path.insert(0, _Pure())
'''


@pytest.fixture
def run_package(tmp_path):
    # what script prints in a Python of its own, run with the files of a compiled package written as the package
    # `compiled`: placed as a checker places it, the whole of each C module it compiles too unless it's concrete; or,
    # pure, imported as it is into a Python with no C modules
    def run(files, script, pure=False, concrete=False):
        write_package(files, tmp_path / "compiled")
        if pure:
            prelude = f"SEMANTICS = {sorted(PYTHON_SEMANTICS)!r}\n{PURE}"
        else:
            prelude = f"WHOLE = {not concrete!r}\n{PLACING}"
        done = subprocess.run([sys.executable, "-c", prelude + script], cwd=tmp_path, capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
        return done.stdout
    return run
