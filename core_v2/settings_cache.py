"""Small process-local cache with invalidation fencing for in-flight reads."""
from collections import OrderedDict


class SettingsCache(OrderedDict):
    def __init__(self):
        super().__init__()
        self.generation = 0

    def pop(self, key, *args):
        # Configuration commands invalidate even when no entry was cached yet.
        self.generation += 1
        return super().pop(key, *args)

    def clear(self):
        self.generation += 1
        super().clear()
