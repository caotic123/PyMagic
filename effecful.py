# example models of random.randint and input, run with PyMagic

import random
import builtins
import itertools

from PyMagic import model, magic, constraint


@model(random)
class _model_Random():
    @model(random.randint)
    def model_randomint(min, max):
        x = magic(int)
        constraint(min <= x <= max)
        return x

@model(builtins)
class _model_Builtins():
    @model(input)
    def _model_input(prompt=''):
        x = magic(str)
        return x

models = [
    _model_Random,
    _model_Builtins,
]

magics = {
    random : {random.randint: lambda min, max : itertools.cycle([int(min + int(max-min)/2)])},
    builtins : {input: lambda prompt='' : itertools.cycle(["lol"])},
}

# the same, setting each magic() by the name of its variable: every x = magic() draws from x's iterable
magics_by_name = {
    random : {random.randint: lambda min, max : {"x": itertools.cycle([max])}},
    builtins : {input: lambda prompt='' : {"x": itertools.cycle([prompt])}},
}

# plain patches go in the models list too: each function is replaced as is, with no model class
simple = {print : lambda str: "lol"}
