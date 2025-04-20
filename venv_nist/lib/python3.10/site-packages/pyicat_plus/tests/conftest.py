import pytest
from .fixtures.icat import *  # noqa F401

from ..metadata.definitions import load_icat_fields


@pytest.fixture
def icat_namespace():
    metadict = dict()

    def getter(key):
        nonlocal metadict
        return metadict[key]

    def setter(key, value):
        nonlocal metadict
        metadict[key] = value

    icat_fields = load_icat_fields()
    metadata = icat_fields.namespace(getter=getter, setter=setter)

    return icat_fields, metadata, metadict
