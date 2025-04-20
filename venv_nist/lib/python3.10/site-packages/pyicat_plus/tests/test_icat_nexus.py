import h5py

from .utils.compare import deep_compare
from ..metadata.nexus import create_nxtreedict
from ..metadata.definitions import load_icat_fields
from ..apps.icat_as_nexus import save_icat_as_nexus


def test_icat_metadata_to_nexus(icat_namespace):
    icat_fields, metadata, metadict = icat_namespace

    metadata.instrument.detector01.name = "diode1"
    metadata.instrument.detector02.name = "diode2"

    metadata.instrument.variables.name = ["roby", "robz"]
    metadata.instrument.variables.value = [0, 0]

    metadata.instrument.insertion_device.gap.name = ["roby", "robz"]
    metadata.instrument.insertion_device.gap.value = [0, 0]

    metadata.instrument.primary_slit.name = "primary_slit"
    metadata.instrument.primary_slit.horizontal_gap = 0
    metadata.instrument.primary_slit.horizontal_offset = 0
    metadata.instrument.primary_slit.vertical_gap = 0
    metadata.instrument.primary_slit.vertical_offset = 0

    metadata.sample.name = "sample"
    metadata.sample.positioners.name = "roby"
    metadata.sample.positioners.value = 0

    metadata.FLUO.i0 = 1
    metadata.FLUO.measurement.i0_start = 0.5
    metadata.definition = "FLUO"

    nxtreedict = create_nxtreedict(metadict, icat_fields=icat_fields)
    expected = {
        "@NX_class": "NXentry",
        "FLUO": {
            "@NX_class": "NXsubentry",
            "i0": 1.0,
            "measurement": {
                "@NX_class": "NXcollection",
                "i0_start": 0.5,
                "i0_start@units": "photons/s",
            },
        },
        "instrument": {
            "@NX_class": "NXinstrument",
            "variables": {
                "@NX_class": "NXcollection",
                "name": ["roby", "robz"],
                "value": [0, 0],
            },
            "insertion_device": {
                "@NX_class": "NXinsertion_device",
                "gap": {
                    "@NX_class": "NXpositioner",
                    "name": ["roby", "robz"],
                    "value": [0, 0],
                },
            },
            "primary_slit": {
                "@NX_class": "NXslit",
                "name": "primary_slit",
                "horizontal_gap": 0,
                "horizontal_offset": 0,
                "vertical_gap": 0,
                "vertical_offset": 0,
            },
            "detector01": {"@NX_class": "NXdetector", "name": "diode1"},
            "detector02": {"@NX_class": "NXdetector", "name": "diode2"},
        },
        "sample": {
            "@NX_class": "NXsample",
            "name": "sample",
            "positioners": {"@NX_class": "NXpositioner", "name": "roby", "value": 0},
        },
        "definition": "FLUO",
    }
    deep_compare(nxtreedict, expected)


def test_icat_metadata_to_hdf5(tmpdir):
    filename = str(tmpdir / "test.h5")
    save_icat_as_nexus(filename)

    top_level_names = set(load_icat_fields())

    with h5py.File(filename, "r") as f:
        assert set(f) == {"entryname"}
        entry = f["entryname"]
        assert set(entry) == top_level_names
