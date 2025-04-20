from packaging.version import Version
from importlib.metadata import version as get_version

import pytest
import numpy
import h5py
from collections.abc import Mapping
from silx.io.dictdump import dicttonx
from blissdata.h5api import static_hdf5
from blissdata.h5api import dynamic_hdf5


def test_static_hdf5(bliss_data_file):
    """Compare the h5py API and the h5py-like Bliss Data API on top of static HDF5 files"""
    kwargs = {"mode": "r"}
    if Version(get_version("h5py")) >= Version("3.5.0"):
        kwargs["locking"] = True

    with h5py.File(bliss_data_file, **kwargs) as root_hdf5:
        with static_hdf5.File(bliss_data_file, mode="r", locking=True) as root_bliss:
            _assert_group_api(root_hdf5, root_bliss)


def test_dynamic_hdf5(bliss_data_file):
    """Compare the h5py API and the h5py-like Bliss Data API on top of static HDF5 files"""
    kwargs = {"mode": "r"}
    if Version(get_version("h5py")) >= Version("3.5.0"):
        kwargs["locking"] = True

    with h5py.File(bliss_data_file, **kwargs) as root_hdf5:
        with dynamic_hdf5.File(
            bliss_data_file, mode="r", locking=True, retry_timeout=0
        ) as root_bliss:
            _assert_group_api(root_hdf5, root_bliss)


def _assert_group_api(group_hdf5: Mapping, group_bliss: Mapping):
    assert group_hdf5.file.filename == group_bliss.file.filename

    assert group_hdf5.name == group_bliss.name
    assert len(group_hdf5) == len(group_bliss)
    assert set(group_hdf5) == set(group_bliss)
    assert set(group_hdf5.keys()) == set(group_bliss.keys())
    names_hdf5, names_bliss = zip(*zip(group_hdf5, group_bliss))
    assert set(names_hdf5) == set(names_bliss)

    for key in group_hdf5:
        node_hdf5 = group_hdf5[key]
        node_bliss = group_bliss[key]
        if isinstance(node_hdf5, h5py.Group):
            _assert_group_api(node_hdf5, node_bliss)
        else:
            _assert_dataset_api(node_hdf5, node_bliss)

    dict_hdf5 = dict(group_hdf5.items())
    dict_bliss = dict(group_bliss.items())
    for key in dict_hdf5:
        node_hdf5 = dict_hdf5[key]
        node_bliss = dict_bliss[key]
        if isinstance(node_hdf5, h5py.Group):
            _assert_group_api(node_hdf5, node_bliss)
        else:
            _assert_dataset_api(node_hdf5, node_bliss)

    _assert_attributes_api(group_hdf5.attrs, group_bliss.attrs)

    if group_bliss.parent is None:
        assert group_hdf5.name == "/"
        assert group_bliss.name == "/"
    else:
        assert group_hdf5.parent.name == group_bliss.parent.name
        assert group_hdf5.parent[group_hdf5.name].name == group_hdf5.name
        assert group_bliss.parent[group_bliss.name].name == group_bliss.name

    assert group_hdf5["/"].name == "/"
    assert group_bliss["/"].name == "/"

    assert group_hdf5.file.name == group_bliss.file.name
    assert group_hdf5.file[group_hdf5.name].name == group_hdf5.name
    assert group_bliss.file[group_bliss.name].name == group_bliss.name

    with pytest.raises(KeyError):
        group_hdf5["non_existent"]
    with pytest.raises(KeyError):
        group_bliss["non_existent"]
    assert "non_existent" not in group_hdf5
    assert "non_existent" not in group_bliss

    with pytest.raises(KeyError):
        group_hdf5.attrs["non_existent"]
    with pytest.raises(KeyError):
        group_bliss.attrs["non_existent"]
    assert "non_existent" not in group_hdf5.attrs
    assert "non_existent" not in group_bliss.attrs


def _assert_dataset_api(dataset_hdf5, dataset_bliss):
    assert dict(dataset_hdf5.attrs) == dict(dataset_bliss.attrs)
    assert dataset_hdf5.dtype == dataset_bliss.dtype
    assert dataset_hdf5.shape == dataset_bliss.shape
    assert dataset_hdf5.size == dataset_bliss.size
    assert dataset_hdf5.ndim == dataset_bliss.ndim

    if dataset_hdf5.ndim == 0:
        with pytest.raises(TypeError):
            len(dataset_hdf5)
        with pytest.raises(TypeError):
            len(dataset_bliss)
    else:
        assert len(dataset_hdf5) == len(dataset_bliss)

    if dataset_hdf5.ndim == 0:
        data_hdf5 = dataset_hdf5[()]
        data_bliss = dataset_bliss[()]
        _assert_data(data_hdf5, data_bliss)
    else:
        for idx in (tuple(), Ellipsis, 0, slice(0, 1)):
            data_hdf5 = dataset_hdf5[idx]
            data_bliss = dataset_bliss[idx]
            _assert_data(data_hdf5, data_bliss)
        for data_hdf5, data_bliss in zip(dataset_hdf5, dataset_bliss):
            _assert_data(data_hdf5, data_bliss)

    if dataset_hdf5.ndim > 0:
        for data_hdf5, data_bliss in zip(dataset_hdf5, dataset_bliss):
            _assert_data(data_hdf5, data_bliss)

    with pytest.raises(KeyError):
        dataset_hdf5.attrs["non_existent"]
    with pytest.raises(KeyError):
        dataset_bliss.attrs["non_existent"]
    assert "non_existent" not in dataset_hdf5.attrs
    assert "non_existent" not in dataset_bliss.attrs


def _assert_data(data_hdf5, data_bliss):
    if isinstance(data_hdf5, numpy.ndarray):
        numpy.testing.assert_array_equal(data_hdf5, data_bliss)
    else:
        assert data_hdf5 == data_bliss


def _assert_attributes_api(attrs_hdf5: Mapping, attrs_bliss: Mapping):
    assert set(attrs_hdf5) == set(attrs_bliss)
    assert set(attrs_hdf5.keys()) == set(attrs_bliss.keys())
    for key in attrs_hdf5:
        node_hdf5 = attrs_hdf5[key]
        node_bliss = attrs_bliss[key]
        assert node_hdf5 == node_bliss

    dict_hdf5 = dict(attrs_hdf5.items())
    dict_bliss = dict(attrs_bliss.items())
    assert dict_hdf5 == dict_bliss


@pytest.fixture(scope="module")
def bliss_data_file(tmpdir_factory):
    """Example file with a structure that resembles Bliss data"""
    tmpdir = tmpdir_factory.mktemp(__name__)
    filename = str(tmpdir / "data.h5")
    data = {
        "1.1": _scan_data(11, "ascan 0 1 10 0.1"),
        "2.1": _scan_data(6, "ascan 0 1 5 0.2"),
        "2.2": _scan_data(7, "ascan 0 1 6 0.3"),
    }
    dicttonx(data, filename)
    return filename


def _prepare_scan(title: str) -> dict:
    return {
        "@NX_class": "NXentry",
        "title": title,
        "instrument": {
            "@NX_class": "NXinstrument",
            "name": "ESRF-ID00",
            "name@short_name": "id00",
            "positioners": {"samx": 0.0, "samy": 1.0, "samz": 2.0},
        },
        "measurement": {},
        "sample": {"@NX_class": "NXsample", "name": "samplename"},
        "writer": {"@NX_class": "NXnote", "status": "SUCCEEDED"},
    }


def _detector_data(npoints: int, detector_shape: tuple) -> dict:
    shape = (npoints,) + detector_shape
    return {"@NX_class": "NXdetector", "data": numpy.random.random(shape)}


def _scan_data(npoints: int, title: str) -> dict:
    data = _prepare_scan(title)
    for name, detector_shape in (
        ("samy", tuple()),
        ("diode", tuple()),
        ("mca", (10,)),
        ("diffcam", (6, 9)),
    ):
        data["instrument"][name] = _detector_data(npoints, detector_shape)
        data["measurement"][name] = f">../instrument/{name}/data"
    return data
