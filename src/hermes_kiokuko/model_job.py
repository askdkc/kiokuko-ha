"""Single model worker, with a file lock that outlives a timed-out caller."""
from contextlib import contextmanager
import contextvars
import os
import threading

from .errors import KiokukoError
from .filesystem import acquire_lock


class ModelJob:
    def __init__(self, fd):
        self.fd = fd
        self.detached = False

    def call(self, function, timeout=12):
        done, guard = threading.Event(), threading.Lock()
        answer = []
        context = contextvars.copy_context()
        def worker():
            try:
                answer.append((True, function()))
            except BaseException as error:
                answer.append((False, error))
            finally:
                with guard:
                    if self.detached:
                        os.close(self.fd)
                    done.set()
        threading.Thread(target=lambda: context.run(worker), daemon=True, name='kiokuko-model-job').start()
        if not done.wait(timeout):
            with guard:
                if not done.is_set():
                    self.detached = True
                    raise KiokukoError('EXPERIENCE_TIMEOUT')
        ok, result = answer[0]
        if not ok:
            raise result
        return result


@contextmanager
def model_job(service):
    job = ModelJob(acquire_lock(service.store.directory/'experience.lock', exclusive=True, timeout=0))
    try:
        yield job
    finally:
        if not job.detached:
            os.close(job.fd)
