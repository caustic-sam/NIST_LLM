from bliss import current_session
from bliss.common.proxy import ProxyWithoutCall

from .base import BasicScanSaving  # noqa F401
from .esrf import ESRFScanSaving  # noqa F401
from .esrf import ESRFDataPolicyEvent  # noqa F401


def ScanSaving(*args, **kwargs):
    scan_saving = current_session.scan_saving
    return scan_saving.__class__(*args, **kwargs)


class ScanSavingProxy(ProxyWithoutCall):
    """Proxy to a scan saving instance.

    Usages:

    .. code:: python

        proxy = ScanSavingProxy()
        proxy._init(BasicScanSaving, name, session_name)
    """

    def __init__(self):
        super().__init__(lambda: None, init_once=True)

    def _init(self, new_cls, *args, **kwargs):
        try:
            object.__delattr__(self, "__target__")
        except AttributeError:
            pass
        object.__setattr__(self, "__scan_saving_class__", new_cls)
        object.__setattr__(self, "__factory__", lambda: new_cls(*args, **kwargs))

    @property
    def __class__(self):
        return self.__scan_saving_class__
