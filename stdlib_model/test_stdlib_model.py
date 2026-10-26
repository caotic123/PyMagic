import errno
import inspect
import pathlib
from types import NoneType

import pytest

from PyMagic import Model, get_models
from stdlib_model import errno as model_errno, models
from stdlib_model.testing import improper_uses

FOLDER = pathlib.Path(__file__).parent
MODEL_FILES = sorted(path for path in FOLDER.rglob("__init__.py") if path.parent != FOLDER)

COPIES = [entry for kind, entry in get_models(models) if kind is Model.COPY]
PLAIN = (int, float, str, bytes, bool, NoneType, tuple, frozenset)
NOT_COPIED = {"__module__", "__dict__", "__weakref__", "__model__", "__model_kind__"}

def members(cls):
    return {name: value for name, value in vars(cls).items() if name not in NOT_COPIED}

@pytest.mark.parametrize("copy", COPIES, ids=[f"{copy.__model__.__module__}.{copy.__name__}" for copy in COPIES])
def test_copy_has_the_properties_of_its_original(copy):
    # its original's members, plain values, docstring and bases, and no other public members: an original in C
    # keeps its state where Python can't, so a copy may add private ones
    original = copy.__model__
    copied, own = members(copy), members(original)
    assert own.keys() <= copied.keys()
    assert {name for name in copied.keys() - own.keys() if not name.startswith("_")} == set()
    assert {name: value for name, value in copied.items() if name in own and isinstance(value, PLAIN)} == \
        {name: value for name, value in own.items() if isinstance(value, PLAIN)}
    for name, value in own.items():
        if inspect.isfunction(value):
            assert inspect.signature(copied[name]).parameters.keys() == inspect.signature(value).parameters.keys()
    assert copy.__doc__ == original.__doc__
    assert [getattr(base, "__model__", base) for base in copy.__bases__] == list(original.__bases__)

@pytest.mark.parametrize("path", MODEL_FILES, ids=[str(path.parent.relative_to(FOLDER)) for path in MODEL_FILES])
def test_model_reaches_its_module_only_through_models(path):
    assert improper_uses(path, models) == []

def test_errno_numbers_are_the_platforms():
    for name in ("EOVERFLOW", "EADDRNOTAVAIL", "ENETUNREACH"):
        assert getattr(model_errno, name) == getattr(errno, name)

def test_every_folder_with_models_is_in_the_models():
    modelled = {pathlib.Path(inspect.getfile(entry)).parent for entry in models}
    with_models = {path.parent for path in MODEL_FILES if "@model(" in path.read_text()}
    assert modelled == with_models
