import builtins
import inspect
from functools import wraps
from . import monkey


# Maps old class to new class
_CLASSES = {}


def _isinstance(old_isinstance, logger=None):
    @wraps(old_isinstance)
    def new_isinstance(obj, classes):
        if old_isinstance(obj, classes):
            return True
        if not old_isinstance(classes, tuple):
            classes = (classes,)
        # Replace registered classes and try again
        classes = tuple(
            o for _class in classes for o in _CLASSES.get(_class, (_class,))
        )
        return old_isinstance(obj, classes)

    return new_isinstance


def _get_base_classes(orgcls, newcls):
    new_classes = inspect.getmro(newcls)
    for _orgcls in inspect.getmro(orgcls):
        if _orgcls not in new_classes:
            yield _orgcls


def register_class(orgcls, newcls):
    for _orgcls in _get_base_classes(orgcls, newcls):
        s = _CLASSES.setdefault(_orgcls, set())
        s.add(newcls)
    print(_CLASSES)


def unregister_class(orgcls, newcls):
    for _orgcls in _get_base_classes(orgcls, newcls):
        s = _CLASSES.setdefault(_orgcls, set())
        s.remove(newcls)


def patch():
    newitem = _isinstance(monkey.original(builtins, "isinstance"))
    monkey.patch_item(builtins, "isinstance", newitem)


def unpatch():
    monkey.unpatch_item(builtins, "isinstance")
