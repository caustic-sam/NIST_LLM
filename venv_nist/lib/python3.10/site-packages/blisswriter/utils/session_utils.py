"""Activate Bliss session utilities
"""

from functools import wraps


def with_scan_saving(func):
    """Pass the current session's SCAN_SAVING instance as a named argument

    :param callable func:
    :returns callable:
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        scan_saving = kwargs.get("scan_saving")
        if scan_saving is None:
            from bliss import current_session  # avoid bliss patching on import

            if current_session:
                kwargs["scan_saving"] = current_session.scan_saving
            else:
                raise RuntimeError("No activate Bliss session")
        return func(*args, **kwargs)

    return wrapper


@with_scan_saving
def scan_saving_get(attr, default=None, scan_saving=None):
    """Get attribute from the session's scan saving object

    :param str attr:
    :param default:
    :param bliss.scanning.scan.ScanSaving scan_saving:
    :returns str:
    """
    return getattr(scan_saving, attr, default)


@with_scan_saving
def dataset_get(attr, default=None, scan_saving=None):
    """Get attribute from the session's dataset object

    :param str attr:
    :param default:
    :param bliss.scanning.scan.ScanSaving scan_saving:
    :returns str:
    """
    try:
        return scan_saving.dataset[attr]
    except (AttributeError, KeyError):
        return default
