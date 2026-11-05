"""
Cancellation Module
Stopping a pipeline run from another thread (the GUI's cancel button): the run's log
and progress callbacks are wrapped so that, once the event is set, the next update
raises PipelineCancelled.
"""


class PipelineCancelled(BaseException):
    """Raised inside a run once its cancel event is set. A BaseException, like
    KeyboardInterrupt, so the stages' `except Exception` fallbacks do not swallow it."""


def cancellable(fn, cancel):
    """`fn`, raising PipelineCancelled instead of running once `cancel` (an Event) is set."""
    def wrapped(*args):
        if cancel.is_set():
            raise PipelineCancelled()
        return fn(*args)
    return wrapped
