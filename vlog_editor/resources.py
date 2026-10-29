"""One CPU-heavy job per app process; not a distributed scheduler."""
from functools import wraps
from threading import Lock

from vlog_editor.media import MediaError

_JOB = Lock()


def bounded_job(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        if not _JOB.acquire(blocking=False):
            raise MediaError("服务器正在处理其他视频任务，请稍后再试。")
        try:
            return function(*args, **kwargs)
        finally:
            _JOB.release()
    return wrapped
