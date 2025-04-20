import pytest

from blissdata.h5api.utils.hdf5 import DynamicHDF5Handler
from blissdata.h5api.utils.bliss import BlissDynamicHDF5Handler


@pytest.fixture
def check_globals():
    assert not DynamicHDF5Handler._INSTANCES
    assert not BlissDynamicHDF5Handler._INSTANCES
    yield
    assert not DynamicHDF5Handler._INSTANCES
    assert not BlissDynamicHDF5Handler._INSTANCES


@pytest.mark.parametrize(
    "first_class", [DynamicHDF5Handler, BlissDynamicHDF5Handler], ids=["base", "bliss"]
)
@pytest.mark.parametrize(
    "second_class", [DynamicHDF5Handler, BlissDynamicHDF5Handler], ids=["base", "bliss"]
)
def test_different_file_handlers(check_globals, first_class, second_class):
    handler1 = first_class("file1.hdf5")
    handler2 = second_class("file2.hdf5")
    assert handler1 is not handler2
    handler1.close()
    handler2.close()


@pytest.mark.parametrize(
    "first_class", [DynamicHDF5Handler, BlissDynamicHDF5Handler], ids=["base", "bliss"]
)
@pytest.mark.parametrize(
    "second_class", [DynamicHDF5Handler, BlissDynamicHDF5Handler], ids=["base", "bliss"]
)
def test_same_file_handlers(check_globals, first_class, second_class):
    handler1 = first_class("file1.hdf5")
    handler2 = second_class("file1.hdf5")
    assert handler1 is handler2
    handler1.close()

    handler3 = second_class("file1.hdf5")
    assert handler1 is handler3
    handler2.close()
    handler3.close()

    handler4 = second_class("file1.hdf5")
    assert handler1 is not handler4
    handler4.close()
