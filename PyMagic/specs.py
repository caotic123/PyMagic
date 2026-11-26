# specs and the @spec decorator: properties of all the arguments of a function, the need() calls that say which
# arguments a spec speaks of and the always() calls it states of them; and exists, a witness of a property

import sys
import itertools
import functools
import dataclasses
from contextvars import ContextVar

from .model import (
    BrokenVariant, Model, RunningUnstatedModel, format_call, name_of, shown, signature_of, source_at,
)


# ------------------------------------------------------------------ errors

class ConditionalPropertyRaise(BaseException):
    # arguments a need() of a spec rejects: the spec says nothing of them, so they are no counterexample to it
    pass

class SpecPropertyInvalid(BaseException):
    # arguments that meet every need() of a spec but break one of its always(): a counterexample to it
    pass

class BrokenExistentialQuantifier(SpecPropertyInvalid):
    # an exists() whose witness doesn't have the property it witnesses
    pass


# ------------------------------------------------------------------ need and always

@dataclasses.dataclass
class SpecCall:
    # a spec being checked for some arguments
    func: object
    args: tuple
    kwargs: dict

    def __str__(self):
        return format_call(self.func, self.args, self.kwargs)

# the spec calls in progress, innermost last
running_specs = ContextVar("running_specs", default=())

def current_spec():
    running = running_specs.get()
    return running[-1] if running else None

def need(condition):
    # the arguments the spec speaks of: those that make the condition false are left out of it, with a
    # ConditionalPropertyRaise whose call is the spec call it is a need() of, or None outside any spec
    if condition:
        return
    where = source_at(sys._getframe(1), "need(...)")
    call = current_spec()
    if call is None:
        error = ConditionalPropertyRaise(f"{where} is unmet, outside any spec")
    else:
        error = ConditionalPropertyRaise(
            f"{call} doesn't meet {where}: {name_of(call.func)} says nothing of these arguments"
        )
    error.call = call
    raise error

def always(condition):
    # what the spec states of every argument it speaks of: arguments that make the condition false are a
    # counterexample to it, raising SpecPropertyInvalid
    if condition:
        return
    where = source_at(sys._getframe(1), "always(...)")
    call = current_spec()
    if call is None:
        raise SpecPropertyInvalid(f"{where} failed outside any spec")
    raise SpecPropertyInvalid(f"{call} broke {where}: a counterexample to {name_of(call.func)}")


# ------------------------------------------------------------------ exists

class exists:
    # that some value has a property, shown by one that does, the witness: exists(is_higher_than, 101). prop is
    # what the property gives for the witness, true or a proof of it, like another exists. Made with a witness that
    # doesn't have the property, it raises a BrokenExistentialQuantifier, which is an always() broken: a spec that
    # makes it has a counterexample. compile.py has it as a package compiled for a model checker has it
    def __init__(self, property, witness):
        self.property = property
        self.witness = witness
        self.prop = property(witness)
        if not self.prop:
            where = source_at(sys._getframe(1), "exists(...)")
            call = current_spec()
            made = "made" if call is None else f"{call} made"
            raise BrokenExistentialQuantifier(
                f"{made} {self!r} at {where}, but {name_of(property)}({shown(witness)}) is {shown(self.prop)}: "
                f"the witness doesn't have the property"
            )

    def __repr__(self):
        return f"exists({name_of(self.property)}, {shown(self.witness)})"


# ------------------------------------------------------------------ specs

@dataclasses.dataclass
class Checked:
    # what forall() found, the values of each case it tried: those the spec held for, and those a need() left out
    held: list = dataclasses.field(default_factory=list)
    unmet: list = dataclasses.field(default_factory=list)

def spec(func=None):
    # marks func as a spec: a property of all its arguments, which its need() calls narrow to those it speaks of
    # and its always() calls state. Called with some, it checks them, returning what func returns, like an exists;
    # forall() checks it for every combination of the values given for each argument. @spec() is @spec
    if func is None:
        return spec
    if not callable(func):
        raise TypeError(f"@spec can only decorate a function, got {func!r} of type {type(func).__name__}")
    func_signature = signature_of(func)

    @functools.wraps(func)
    def checked(*args, **kwargs):
        call = SpecCall(func, args, kwargs)
        token = running_specs.set(running_specs.get() + (call,))
        try:
            return func(*args, **kwargs)
        except ConditionalPropertyRaise as error:
            if getattr(error, "call", None) is call:
                raise
            # another spec it calls says nothing of what it's given: a step of this one that doesn't hold
            raise SpecPropertyInvalid(f"{call} relies on what another spec says nothing of: {error}") from error
        except (RunningUnstatedModel, BrokenVariant) as error:
            # a model it runs wasn't given its values, or not ones its original could give: the user gives every
            # value, and the error says which spec call wanted them, the innermost one
            if getattr(error, "spec_call", None) is None:
                error.spec_call = call
                if len(error.args) == 1 and isinstance(error.args[0], str):
                    error.args = (f"checking {call}: {error.args[0]}",)
            raise
        finally:
            running_specs.reset(token)

    def forall(*domains, **named_domains):
        # the spec checked for every combination of the values in domains, one domain for each argument, by
        # position or by name: the first combination breaking it raises its SpecPropertyInvalid
        if func_signature is not None:
            try:
                func_signature.bind(*domains, **named_domains)
            except TypeError as error:
                raise TypeError(
                    f"forall() of {name_of(func)}{func_signature} takes one domain for each argument: {error}"
                ) from None
        names = list(named_domains)
        found = Checked()
        for values in itertools.product(*domains, *named_domains.values()):
            args, kwargs = values[:len(domains)], dict(zip(names, values[len(domains):]))
            try:
                checked(*args, **kwargs)
            except ConditionalPropertyRaise:
                found.unmet.append(values)
            else:
                found.held.append(values)
        return found

    forall.__forall_of__ = checked  # so run_spec runs it as the spec's
    checked.forall = forall
    checked.__model_kind__ = Model.SPEC
    return checked
