"""Opt-in local measurements; never retain request text or identity values."""
import time


class Measurements:
    def __init__(self, output=None):
        self.output = output
        self.started = self.last = time.perf_counter() if output is not None else 0

    def mark(self, stage):
        if self.output is not None:
            current = time.perf_counter()
            self.output[stage + '_ms'] = (current - self.last) * 1000
            self.last = current

    def finish(self):
        if self.output is not None:
            self.output['total_ms'] = (time.perf_counter() - self.started) * 1000
