# run_model, run_spec and their helpers: swapping models in for their originals while a program or a spec
# runs

import sys
from inspect import isroutine, unwrap
from contextlib import ExitStack
from unittest import mock

from .model import (
    Model, MissedModelObject, get_models, model_members, name_of, running_copies, signature_of, stand_in,
    transplanted,
)


# ------------------------------------------------------------------ patching

def patch_everywhere(stack, everywhere):
    # patch every loaded module that references an original (covers from-imports), in one pass over the modules
    # for all of them: everywhere is {id(original): (original, replacement)}
    patched = set()
    for module in list(sys.modules.values()):
        for name, value in list(getattr(module, "__dict__", {}).items()):
            replacing = everywhere.get(id(value))
            if replacing is not None and value is replacing[0]:
                stack.enter_context(mock.patch.object(module, name, replacing[1]))
                patched.add(id(value))
    for key, (original, _) in everywhere.items():
        if key not in patched:
            raise MissedModelObject(
                f"no loaded module references {name_of(original)!s}, so there is nothing to patch; patch the "
                f"function as a module attribute (e.g. random.randint), not a copy or a fresh bound method"
            )

def replace_everywhere(everywhere, original, replacement):
    everywhere[id(original)] = (original, replacement)

def patch_functions(everywhere, replacements):
    for original, replacement in replacements.items():
        if not isroutine(original):
            raise MissedModelObject(
                f"plain patches only replace functions, got {original!r} of type {type(original).__name__}; "
                f"use a @model class to model a class or a module"
            )
        if not callable(replacement):
            raise MissedModelObject(
                f"the replacement for {name_of(original)} must be callable, "
                f"got {replacement!r} of type {type(replacement).__name__}"
            )
        replace_everywhere(everywhere, original, replacement)

def propagate_magic_values(stack, everywhere, func, original, magic_generator, is_method, owner=None, name=None):
    # swaps a model function in for its original, with its magic() calls drawing from magic_generator:
    # a method replaces its attribute on the owner class now, a function replaces the original everywhere once
    # all the models are in
    if original is not None:
        func = stand_in(unwrap(func), original, signature_of(original), magic_generator)
    if is_method:
        stack.enter_context(mock.patch.object(owner, original.__name__ if original else name, func, create=True))
    else:
        replace_everywhere(everywhere, original, func)


# ------------------------------------------------------------------ running models

def magic_generator_for(magics, owner, original, used):
    # the magic generator given for original under owner, noting it as used
    if original is None:
        return None
    generator = magics.get(owner, {}).get(original)
    if generator is not None:
        used.add((owner, original))
    return generator

def as_original(error):
    # an exception raised in the run as it leaves it: a copy becomes the exception it copies, with its arguments,
    # attributes, cause and traceback
    if getattr(type(error), "__model_kind__", None) is not Model.COPY:
        return error
    return transplanted(error, type(error).__model__)

def check_all_magics_used(magics, used, models):
    unknown = [
        f"{getattr(owner, '__name__', owner)}.{getattr(function, '__name__', function)}"
        for owner, functions in magics.items() for function in functions
        if (owner, function) not in used
    ]
    if unknown:
        model_names = [m.__name__ for m in models if not isinstance(m, dict)]
        raise MissedModelObject(f"magics given for {unknown}, but no model in {model_names} models them")

def spec_run_by(target):
    # the spec a target is, or whose forall() it is, or None
    spec = getattr(target, "__forall_of__", target)
    return spec if getattr(spec, "__model_kind__", None) is Model.SPEC else None

def run_model(models, magics=None):
    # models holds @model classes, @model functions and plain {function: replacement} dicts; the target is a
    # program, and a spec is run with run_spec
    def func(target, *args, **kwargs):
        spec = spec_run_by(target)
        if spec is not None:
            raise MissedModelObject(f"{name_of(spec)} is a spec; run it with run_spec(models, magics), not run_model")
        return running(models, magics or {}, target, args, kwargs)
    return func

def run_spec(models, magics=None):
    # a spec, or its forall(), run as run_model runs a program: the models in place of their originals, and their
    # magic() values from the magics. The user gives every value: one not given raises RunningUnstatedModel, and
    # one the original couldn't give BrokenVariant, each naming the spec call that wanted it
    def func(target, *args, **kwargs):
        if spec_run_by(target) is None:
            raise MissedModelObject(
                f"run_spec runs a spec or its forall(), got {name_of(target)}; run it with run_model(models, magics)"
            )
        return running(models, magics or {}, target, args, kwargs)
    return func

def running(models, magics, target, args, kwargs):
    # target called with args and kwargs, the models in place of their originals while it runs
    used, everywhere = set(), {}
    with ExitStack() as stack:
        for kind, entry in get_models(models):
            match kind:
                case Model.COPY:
                    # code in the run makes, checks and catches the copy in place of the original; its @model
                    # methods draw from the magics of the class it copies, as a class model's do
                    replace_everywhere(everywhere, entry.__model__, entry)
                    for name, method, original in model_members(entry):
                        if original is not None:
                            generator = magic_generator_for(magics, entry.__model__, original, used)
                            propagate_magic_values(stack, everywhere, method, original, generator,
                                                   is_method=True, owner=entry, name=name)
                case Model.CLASS:
                    # patch the class object itself, so every module and existing instance sees it
                    owner = entry.__model__
                    for name, method, original in model_members(entry):
                        generator = magic_generator_for(magics, owner, original, used)
                        propagate_magic_values(stack, everywhere, method, original, generator, is_method=True,
                                               owner=owner, name=name)
                case Model.MODULE:
                    # only the @model functions are swapped in; the rest are helpers
                    owner = getattr(entry, "__model__", None)
                    for _, function, original in model_members(entry):
                        if original is not None:
                            generator = magic_generator_for(magics, owner, original, used)
                            propagate_magic_values(stack, everywhere, function, original, generator,
                                                   is_method=False)
                case Model.FUNCTION:
                    # a model function on its own takes its magics from the module of its original
                    original = entry.__model__
                    owner = sys.modules.get(getattr(original, "__module__", None))
                    generator = magic_generator_for(magics, owner, original, used)
                    propagate_magic_values(stack, everywhere, entry, original, generator, is_method=False)
                case Model.PATCHES:
                    patch_functions(everywhere, entry)
                case Model.SPEC:
                    pass  # it models nothing: the models list can hold the specs compiled with them
        patch_everywhere(stack, everywhere)
        copies = {entry.__model__: entry for kind, entry in get_models(models) if kind is Model.COPY}
        token = running_copies.set({**running_copies.get(), **copies})
        stack.callback(running_copies.reset, token)
        check_all_magics_used(magics, used, models)
        try:
            return target(*args, **kwargs)
        except BaseException as error:
            raised = error
    # raised once the originals are back, out of any except block, so it keeps the context it had
    raise as_original(raised)
