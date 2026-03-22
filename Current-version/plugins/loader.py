"""Simple plugin discovery and manager.

Plugins are simple modules under the `plugins` package that expose a `Plugin`
class. The manager will instantiate the class and expose instances by their
`name` attribute.
"""
import importlib
import pkgutil
import plugins


class PluginManager:
    def __init__(self):
        self.plugins = {}

    def discover_plugins(self):
        """Discover and load plugin modules under the `plugins` package."""
        for finder, modname, ispkg in pkgutil.iter_modules(plugins.__path__):
            # skip package init & loader/base files
            if modname in ('__init__', 'base', 'loader'):
                continue

            try:
                module = importlib.import_module(f"plugins.{modname}")
                PluginClass = getattr(module, 'Plugin', None)
                if PluginClass:
                    inst = PluginClass()
                    name = getattr(inst, 'name', modname)
                    self.plugins[name] = inst
            except Exception:
                # Keep discovery tolerant; caller can inspect logs
                continue

    def get_plugin(self, name):
        return self.plugins.get(name)
