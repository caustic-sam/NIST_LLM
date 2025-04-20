# import contextvars
import gevent
from prompt_toolkit.application import current


_old_current_app_session = current._current_app_session


class _LocalAppSession:  # contextvars.ContextVar:
    """Patch prompt-toolkit for compatibility with gevent

    `AppSession` is stored with `contextvars`. This class provides
    a wrapper for `gevent` local storage instead.
    """

    def get(self):
        current_greenlet = gevent.getcurrent()
        if isinstance(current_greenlet, gevent.Greenlet):
            app_session = current_greenlet.spawn_tree_locals.get("app_session")
            if app_session is not None:
                return app_session
        return _old_current_app_session.get()

    def set(self, value):
        return _old_current_app_session.set(value)

    def reset(self, token):
        return _old_current_app_session.reset(token)


_local_app_session = _LocalAppSession()


def patch():
    current._current_app_session = _local_app_session
