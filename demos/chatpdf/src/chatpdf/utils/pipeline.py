from __future__ import annotations

class Pipeline:
    def __init__(self, steps):
        self.steps = list(steps)

    def __or__(self, other):
        if isinstance(other, Pipeline):
            return Pipeline(self.steps + other.steps)
        return Pipeline(self.steps + [other])

    def __call__(self, data, **kwargs):
        for step in self.steps:
            if isinstance(data, tuple):
                data = step(*data, **kwargs)
            else:
                data = step(data, **kwargs)
        return data

class Pipeable:
    def __or__(self, other):
        return Pipeline([self]) | other


