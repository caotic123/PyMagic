# the command line: python -m PyMagic compile MODELS [-o DIRECTORY] [--concrete] [--no-C-spec] [--pure] [--force]

import sys
import argparse
import importlib
import pathlib

from .compile import Compilation, UncompilableModel, compile_models, write_package


def models_named(reference):
    # the models a reference names: module:attribute, or a module whose models list is taken; a single model is
    # a list of one
    module_name, _, attribute = reference.partition(":")
    found = importlib.import_module(module_name)
    for part in (attribute or "models").split("."):
        found = getattr(found, part)
    return list(found) if isinstance(found, (list, tuple)) else [found]


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m PyMagic", description="PyMagic: models of effectful functions")
    commands = parser.add_subparsers(dest="command", required=True)
    compiling = commands.add_parser(
        "compile", help="compile models to a package of Python modules",
        description="Compile models to a package: a module for each module they stand in for, and an __init__.py "
                    "with the tables of the originals they stand in for, which whoever runs the package puts them in "
                    "place of. For a model checker, magic() is left for it to supply.",
    )
    compiling.add_argument("models", help="module:attribute naming a model or a list of them, or a module whose "
                                          "`models` list is compiled (e.g. http_model, stdlib_model.random:_model_random)")
    compiling.add_argument("-o", "--output", metavar="DIRECTORY",
                           help="the package's directory, written; without it, its files are printed")
    compiling.add_argument("--concrete", action="store_true",
                           help="call the real effects in place of magic(), rather than compile for a model checker")
    compiling.add_argument("--no-C-spec", dest="no_c_spec", action="store_true",
                           help="refuse a package that needs a C module of the standard library, through any module "
                                "it imports, but those Python's own semantics rest on")
    compiling.add_argument("--pure", action="store_true",
                           help="compile to run in pure Python: --no-C-spec, every module checked to compile, and an "
                                "__init__.py of comments alone, naming the real modules")
    compiling.add_argument("--force", action="store_true", help="write into an output directory that isn't empty")
    arguments = parser.parse_args(argv)

    try:
        models = models_named(arguments.models)
    except (ImportError, AttributeError) as error:
        parser.error(f"no models at {arguments.models}: {error}")
    compilation = Compilation.CONCRETE if arguments.concrete else Compilation.MODEL_CHECKER
    try:
        files = compile_models(models, compilation, no_c_spec=arguments.no_c_spec, pure=arguments.pure)
    except UncompilableModel as error:
        print(f"PyMagic: {error}", file=sys.stderr)
        return 1

    if arguments.output is None:
        for path, source in files.items():
            print(f"# ---------------------------------------------------------------- {path}\n{source}")
        return 0
    output = pathlib.Path(arguments.output)
    if output.exists() and any(output.iterdir()) and not arguments.force:
        print(f"PyMagic: {output} isn't empty; use --force to write into it", file=sys.stderr)
        return 1
    write_package(files, output)
    print(f"wrote {len(files)} files to {output}")
    return 0
