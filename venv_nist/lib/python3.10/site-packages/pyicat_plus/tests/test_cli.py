from pathlib import Path

from ..apps import store_raw
from ..apps import store_processed
from .utils.message import assert_dataset_message


def test_store_raw(icat_metadata_client, icat_backend):
    _, messages = icat_metadata_client

    argv = [
        "",
        "--beamline",
        "id00",
        "--proposal",
        "hg123",
        "--dataset",
        "datasetname",
        "--path",
        _dummy_path("dataset"),
        "--sample",
        "samplename",
        "-p",
        "field1=value1",
        "-p",
        "field2=[1, 2, 3]",
        "--queue",
        icat_backend["metadata_urls"][0],
    ]
    store_raw.main(argv)

    message = messages.get(timeout=10)
    assert messages.empty()

    expected = {
        "dataset": {
            "@complete": "true",
            "@xmlns": {"tns": "http://www.esrf.fr/icat"},
            "instrument": "id00",
            "investigation": "hg123",
            "location": _dummy_path("dataset"),
            "name": "datasetname",
            "parameter": [
                {"name": "field1", "value": "value1"},
                {"name": "field2", "value": "1,2,3"},
                {"name": "Sample_name", "value": "samplename"},
            ],
            "sample": {"name": "samplename"},
        }
    }
    assert_dataset_message(message, expected)


def test_store_raw_metadatafile(icat_metadata_client, icat_backend, tmpdir):
    metadatafile = str(tmpdir / "metadata.txt")

    with open(metadatafile, "w") as f:
        f.write("field1=value1\n")
        f.write("field2=[1, 2, 3]\n")

    _, messages = icat_metadata_client

    argv = [
        "",
        "--beamline",
        "id00",
        "--proposal",
        "hg123",
        "--dataset",
        "datasetname",
        "--path",
        _dummy_path("dataset"),
        "--sample",
        "samplename",
        "--metadatafile",
        metadatafile,
        "--queue",
        icat_backend["metadata_urls"][0],
    ]
    store_raw.main(argv)

    message = messages.get(timeout=10)
    assert messages.empty()

    expected = {
        "dataset": {
            "@complete": "true",
            "@xmlns": {"tns": "http://www.esrf.fr/icat"},
            "instrument": "id00",
            "investigation": "hg123",
            "location": _dummy_path("dataset"),
            "name": "datasetname",
            "parameter": [
                {"name": "field1", "value": "value1"},
                {"name": "field2", "value": "1,2,3"},
                {"name": "Sample_name", "value": "samplename"},
            ],
            "sample": {"name": "samplename"},
        }
    }
    assert_dataset_message(message, expected)


def test_store_processed(icat_metadata_client, icat_backend):
    _, messages = icat_metadata_client

    argv = [
        "",
        "--beamline",
        "id00",
        "--proposal",
        "hg123",
        "--dataset",
        "datasetname",
        "--path",
        _dummy_path("processed"),
        "--sample",
        "samplename",
        "-p",
        "field1=value1",
        "-p",
        "field2=[1, 2, 3]",
        "--queue",
        icat_backend["metadata_urls"][0],
        "--raw",
        _dummy_path("dataset1"),
        "--raw",
        _dummy_path("dataset2"),
    ]
    store_processed.main(argv)

    expected = {
        "dataset": {
            "@complete": "true",
            "@xmlns": {"tns": "http://www.esrf.fr/icat"},
            "instrument": "id00",
            "investigation": "hg123",
            "location": _dummy_path("processed"),
            "name": "datasetname",
            "parameter": [
                {"name": "field1", "value": "value1"},
                {"name": "field2", "value": "1,2,3"},
                {"name": "Sample_name", "value": "samplename"},
                {
                    "name": "input_datasets",
                    "value": f"{_dummy_path('dataset1')},{_dummy_path('dataset2')}",
                },
            ],
            "sample": {"name": "samplename"},
        }
    }
    message = messages.get(timeout=10)
    assert messages.empty()

    assert_dataset_message(message, expected)


def _dummy_path(dirname: str) -> str:
    return str(Path.home() / dirname)
