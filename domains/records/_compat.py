"""Legacy ordinary-import and CLI adapters; canonical modules own all state."""
from importlib import import_module
import runpy
import sys


def export(namespace, module_name):
    name = namespace["__name__"]
    if name == "__main__":
        runpy.run_module(module_name, run_name="__main__", alter_sys=True)
        return
    module = import_module(module_name)
    # Existing file-spec callers can read exports; known callers use canonical
    # imports so monkeypatches and class identities are shared, not copied.
    namespace.update(vars(module))
    sys.modules[name] = module
