import os
import re
import time
import logging
import datetime
from typing import Generator, Tuple, Optional, Callable
from contextlib import contextmanager

import h5py

from ..apps import sync_raw
from ..utils import sync_types
from ..utils import path_utils
from ..client.main import IcatClient


def test_unregistered_datasets(tmpdir, icat_main_client):
    client, messages = icat_main_client
    root_dir = str(tmpdir)

    exp_session_test = _init_unregistered_experiment(
        client, messages, root_dir, "hg333", "id99"
    )

    args = client, "hg333", "id99", "20231019"
    kwargs = {"root_dir": root_dir, "raw_data_format": "esrfv3"}

    exp_session = sync_raw.sync_session(*args, **kwargs)
    assert len(exp_session.datasets["registered"]) == 0
    assert len(exp_session.datasets["unregistered"]) == 6

    if exp_session != exp_session_test:
        assert exp_session.as_dict() == exp_session_test.as_dict()

    first_datasets = exp_session_test.datasets["unregistered"][:3]
    last_datasets = exp_session_test.datasets["unregistered"][3:]

    for dataset in first_datasets:
        _store_dataset(client, dataset)
        messages.get(timeout=10)

    exp_session = sync_raw.sync_session(*args, **kwargs)
    assert len(exp_session.datasets["registered"]) == 3
    assert len(exp_session.datasets["unregistered"]) == 3

    for dataset in last_datasets:
        _store_dataset(client, dataset)
        messages.get(timeout=10)

    exp_session = sync_raw.sync_session(*args, **kwargs)
    assert len(exp_session.datasets["registered"]) == 6
    assert len(exp_session.datasets["unregistered"]) == 0


def test_unregistered_datasets_content(tmpdir, icat_main_client):
    client, messages = icat_main_client

    root_dir = str(tmpdir)
    _ = _init_unregistered_experiment(client, messages, root_dir, "hg333", "id99")

    exp_session = sync_raw.sync_session(
        client, "hg333", "id99", "20231019", root_dir=root_dir, raw_data_format="esrfv3"
    )

    unregistered = [dset.as_dict() for dset in exp_session.datasets["unregistered"]]

    date_to_string = _astimezone_string(datetime.timezone.utc, iso=True)
    for dataset in unregistered:
        dataset["path"] = os.path.relpath(dataset["path"], root_dir)
        dataset["raw_root_dir"] = os.path.relpath(dataset["raw_root_dir"], root_dir)

        dataset["startdate"] = date_to_string(dataset["startdate"])
        dataset["enddate"] = date_to_string(dataset["enddate"])

        metadata = dataset["metadata"]
        metadata["startDate"] = date_to_string(metadata["startDate"])
        metadata["endDate"] = date_to_string(metadata["endDate"])

    expected = [
        {
            "beamline": "id99",
            "enddate": "2023-10-19T21:00:00+00:00",
            "icat_dataset": None,
            "metadata": {
                "Sample_name": "too_soon_sample",
                "endDate": "2023-10-19T21:00:00+00:00",
                "startDate": "2023-10-19T06:00:01+00:00",
            },
            "name": "0001",
            "path": "hg333/id99/20231019/RAW_DATA/too_soon_collection/too_soon_collection_0001",
            "proposal": "hg333",
            "raw_root_dir": "hg333/id99/20231019/RAW_DATA",
            "startdate": "2023-10-19T05:00:00+00:00",
            "status_reason": [
                "started 1:00:00 before the start of the session",
            ],
        },
        {
            "beamline": "id99",
            "enddate": "2023-10-20T16:00:00+00:00",
            "icat_dataset": None,
            "metadata": {
                "Sample_name": "first_sample",
                "endDate": "2023-10-20T16:00:00+00:00",
                "startDate": "2023-10-20T00:00:00+00:00",
            },
            "name": "0001",
            "path": "hg333/id99/20231019/RAW_DATA/first_collection/first_collection_0001",
            "proposal": "hg333",
            "raw_root_dir": "hg333/id99/20231019/RAW_DATA",
            "startdate": "2023-10-20T00:00:00+00:00",
            "status_reason": [],
        },
        {
            "beamline": "id99",
            "enddate": "2023-10-21T09:00:00+00:00",
            "icat_dataset": None,
            "metadata": {
                "Sample_name": "first_sample",
                "endDate": "2023-10-21T09:00:00+00:00",
                "startDate": "2023-10-20T17:00:00+00:00",
            },
            "name": "0002",
            "path": "hg333/id99/20231019/RAW_DATA/first_collection/first_collection_0002",
            "proposal": "hg333",
            "raw_root_dir": "hg333/id99/20231019/RAW_DATA",
            "startdate": "2023-10-20T17:00:00+00:00",
            "status_reason": [],
        },
        {
            "beamline": "id99",
            "enddate": "2023-10-22T19:00:00+00:00",
            "icat_dataset": None,
            "metadata": {
                "Sample_name": "second_sample",
                "endDate": "2023-10-22T19:00:00+00:00",
                "startDate": "2023-10-22T03:00:00+00:00",
            },
            "name": "0001",
            "path": "hg333/id99/20231019/RAW_DATA/second_collection/second_collection_0001",
            "proposal": "hg333",
            "raw_root_dir": "hg333/id99/20231019/RAW_DATA",
            "startdate": "2023-10-22T03:00:00+00:00",
            "status_reason": [],
        },
        {
            "beamline": "id99",
            "enddate": "2023-10-23T12:00:00+00:00",
            "icat_dataset": None,
            "metadata": {
                "Sample_name": "second_sample",
                "endDate": "2023-10-23T12:00:00+00:00",
                "startDate": "2023-10-22T20:00:00+00:00",
            },
            "name": "0002",
            "path": "hg333/id99/20231019/RAW_DATA/second_collection/second_collection_0002",
            "proposal": "hg333",
            "raw_root_dir": "hg333/id99/20231019/RAW_DATA",
            "startdate": "2023-10-22T20:00:00+00:00",
            "status_reason": [],
        },
        {
            "beamline": "id99",
            "enddate": "2023-10-24T07:00:00+00:00",
            "icat_dataset": None,
            "metadata": {
                "Sample_name": "too_late_collection",
                "endDate": "2023-10-24T05:59:59+00:00",
                "startDate": "2023-10-23T13:00:00+00:00",
            },
            "name": "0001",
            "path": "hg333/id99/20231019/RAW_DATA/too_late_collection/too_late_collection_0001",
            "proposal": "hg333",
            "raw_root_dir": "hg333/id99/20231019/RAW_DATA",
            "startdate": "2023-10-23T13:00:00+00:00",
            "status_reason": [
                "ended 1:00:00 after the end of the session",
            ],
        },
    ]

    for dataset in expected:
        for k in ("path", "raw_root_dir"):
            dataset[k] = dataset[k].replace("/", os.path.sep)

    assert unregistered == expected


def test_missing_files_datasets(tmpdir, icat_main_client, icat_add_files_client):
    client, messages = icat_main_client
    client_add, messages_add = icat_add_files_client
    root_dir = str(tmpdir)

    _ = _init_empty_files_experiment(client, messages, root_dir, "md444", "id99")

    args = client, "md444", "id99", "20230201"
    kwargs = {"root_dir": root_dir, "raw_data_format": "esrfv3"}

    exp_session = sync_raw.sync_session(*args, **kwargs)
    assert len(exp_session.datasets["registered"]) == 1
    assert len(exp_session.datasets["registered_without_files"]) == 1
    assert len(exp_session.datasets["unregistered"]) == 0

    for dataset in exp_session.datasets["registered_without_files"]:
        client_add.add_files(dataset.icat_dataset.icat_dataset_id)
        messages_add.get(timeout=10)

    exp_session = sync_raw.sync_session(*args, **kwargs)
    assert len(exp_session.datasets["registered"]) == 2
    assert len(exp_session.datasets["registered_without_files"]) == 0
    assert len(exp_session.datasets["unregistered"]) == 0


def test_session_serialization(tmpdir, icat_main_client):
    client, messages = icat_main_client

    root_dir = str(tmpdir)
    _ = _init_unregistered_experiment(client, messages, root_dir, "hg333", "id99")

    args = client, "hg333", "id99", "20231019"
    kwargs = {"root_dir": root_dir, "raw_data_format": "esrfv3"}

    exp_session = sync_raw.sync_session(*args, **kwargs)

    exp_session_copy = exp_session.from_dict(exp_session.as_dict())
    assert exp_session == exp_session_copy


def test_session_pprint(caplog, tmpdir, icat_main_client):
    client, messages = icat_main_client

    root_dir = str(tmpdir)
    _ = _init_unregistered_experiment(client, messages, root_dir, "hg333", "id99")

    args = client, "hg333", "id99", "20231019"
    kwargs = {"root_dir": root_dir, "raw_data_format": "esrfv3"}

    exp_session = sync_raw.sync_session(*args, **kwargs)

    for dataset in exp_session.datasets["unregistered"][:3]:
        _store_dataset(client, dataset)
        messages.get(timeout=10)

    exp_session = sync_raw.sync_session(*args, **kwargs)

    date_to_string = _astimezone_string(
        exp_session.icat_investigation.startdate.tzinfo, iso=False
    )
    startdate = date_to_string(exp_session.icat_investigation.startdate)
    enddate = date_to_string(exp_session.icat_investigation.enddate)

    expected = f"""
--------------------------------

Registered datasets:
  /hg333/id99/20231019/RAW_DATA/too_soon_collection/too_soon_collection_0001/
  /hg333/id99/20231019/RAW_DATA/first_collection/first_collection_0001/
  /hg333/id99/20231019/RAW_DATA/first_collection/first_collection_0002/

Invalid datasets:
  /hg333/id99/20231019/RAW_DATA/empty_file/empty_file_0001/
   Cannot extract dataset metadata: HDF5 reading error (HDF5 file not created by Bliss)

Unregistered datasets:
  /hg333/id99/20231019/RAW_DATA/second_collection/second_collection_0001/
  /hg333/id99/20231019/RAW_DATA/second_collection/second_collection_0002/
  /hg333/id99/20231019/RAW_DATA/too_late_collection/too_late_collection_0001/
   ended 1:00:00 after the end of the session

Datasets (TODO):
 3 registered
 1 invalid
 0 not uploaded
 3 unregistered
 0 registered without files

Directory: /hg333/id99/20231019/RAW_DATA/
 Start time: 2023-10-19

Investigation ID: 0
 URL: https://data.esrf.fr/investigation/0/datasets
 Search URL: https://data.esrf.fr/beamline/id99?search=hg333
 Start time: {startdate} <REPLACED>
 End time: {enddate} <REPLACED>
 Duration: 5 days, 0:00:00

--------------------------------
"""

    expected = re.sub(
        r"(/hg333[^\n]*)",
        lambda match: match.group(0).replace("/", os.path.sep),
        expected,
    )

    caplog.set_level(logging.DEBUG, logger="ICAT SYNC")
    exp_session.pprint()
    captured = "\n".join(caplog.messages)
    captured = captured.replace(root_dir, "")

    # Remove the sub-strings that depend on the time this test is run
    captured = re.sub(r"\(.*ago\)", "<REPLACED>", captured)

    assert captured == expected


class _ExperimentalSession(sync_types.ExperimentalSession):
    def create_dataset(
        self,
        collection: str,
        dataset: str,
        sample_name: str,
        startdate: datetime.datetime,
        duration: datetime.timedelta,
        status_reason: Tuple[str] = tuple(),
        has_content: bool = True,
    ) -> sync_types.Dataset:
        """Create Bliss dataset directory with HDF5 file. The file contains 3 scans or nothing."""
        with _init_dataset(self.raw_root_dir, collection, dataset) as nxroot:
            dataset_dir = os.path.dirname(nxroot.filename)
            if has_content:
                nxroot.attrs["creator"] = "Bliss"
                nxroot.attrs["file_time"] = startdate.isoformat()

                now, scan_duration, deadtime = _chunk_duration(
                    startdate, startdate + duration, 3
                )

                enddate = _save_scan(nxroot, now, scan_duration, sample_name)

                now += scan_duration + deadtime
                enddate = _save_scan(
                    nxroot, now, scan_duration, sample_name, failed=True
                )

                now += scan_duration + deadtime
                enddate = _save_scan(nxroot, now, scan_duration, sample_name)
            time.sleep(0.050)  # sync_raw sorts by creation time

        if has_content:
            medata_startdate = startdate
            medata_enddate = enddate

            # See sync_raw metadata extraction from HDF5
            if medata_enddate and medata_startdate <= self.icat_investigation.startdate:
                medata_startdate = (
                    self.icat_investigation.startdate + datetime.timedelta(seconds=1)
                )
            if medata_enddate and medata_enddate >= self.icat_investigation.enddate:
                medata_enddate = self.icat_investigation.enddate - datetime.timedelta(
                    seconds=1
                )

            metadata = {
                "Sample_name": sample_name,
                "startDate": medata_startdate,
                "endDate": medata_enddate,
            }
        else:
            startdate = None
            enddate = None
            metadata = None

        dataset = sync_types.Dataset(
            path=dataset_dir,
            proposal=self.proposal,
            beamline=self.beamline,
            name=dataset,
            raw_root_dir=self.raw_root_dir,
            status_reason=list(status_reason),
            startdate=startdate,
            enddate=enddate,
            metadata=metadata,
        )
        if has_content:
            assert dataset.get_status() == "unregistered"
        else:
            assert dataset.get_status() == "invalid"
        self.add_dataset(dataset)
        return dataset


def _init_unregistered_experiment(
    client: IcatClient, messages, root_dir: str, proposal: str, beamline: str
) -> _ExperimentalSession:
    """Create several datasets on disk:

    - 1 normal dataset starting after the startdate (uregistered)
    - 2 normal datasets (uregistered)
    - 1 dataset with an empty file (uregistered)
    - 2 normal datasets (uregistered)
    - 1 normal dataset ending after the enddate (uregistered)
    """
    startdate = _as_datetime("2023-10-19T08:00:00+02:00")
    enddate = startdate + datetime.timedelta(days=5)

    exp_session = _start_investigation(
        client, messages, root_dir, proposal, beamline, startdate, enddate
    )

    now, dataset_duration, deadtime = _chunk_duration(
        startdate, enddate, 7, deadtime=3600
    )

    # Dataset starts before the startdate
    exp_session.create_dataset(
        "too_soon_collection",
        "0001",
        "too_soon_sample",
        now - 2 * deadtime,
        dataset_duration,
        ["started 1:00:00 before the start of the session"],
    )

    now += dataset_duration + deadtime

    # Normal
    _ = exp_session.create_dataset(
        "first_collection", "0001", "first_sample", now, dataset_duration
    )

    now += dataset_duration + deadtime

    # Normal
    _ = exp_session.create_dataset(
        "first_collection", "0002", "first_sample", now, dataset_duration
    )

    now += dataset_duration + deadtime

    # Empty HDF5 file
    _ = exp_session.create_dataset(
        "empty_file",
        "0001",
        "empty_sample",
        now,
        dataset_duration,
        has_content=False,
        status_reason=[
            "Cannot extract dataset metadata: HDF5 reading error (HDF5 file not created by Bliss)"
        ],
    )

    now += dataset_duration + deadtime

    # Normal
    _ = exp_session.create_dataset(
        "second_collection", "0001", "second_sample", now, dataset_duration
    )

    now += dataset_duration + deadtime

    # Normal
    _ = exp_session.create_dataset(
        "second_collection", "0002", "second_sample", now, dataset_duration
    )

    now += dataset_duration + deadtime

    # Dataset ends after the enddate
    _ = exp_session.create_dataset(
        "too_late_collection",
        "0001",
        "too_late_collection",
        now,
        dataset_duration + 2 * deadtime,
        ["ended 1:00:00 after the end of the session"],
    )

    return exp_session


def _init_empty_files_experiment(
    client: IcatClient, messages, root_dir: str, proposal: str, beamline: str
):
    """Create several datasets on disk:

    - 1 normal dataset (registered)
    - 1 normal dataset (registered without files)
    """
    startdate = _as_datetime("2023-02-01T08:00:00+01:00")
    enddate = startdate + datetime.timedelta(days=5)

    exp_session = _start_investigation(
        client, messages, root_dir, proposal, beamline, startdate, enddate
    )

    now, dataset_duration, deadtime = _chunk_duration(
        startdate, enddate, 2, deadtime=3600
    )

    # Normal
    dataset = exp_session.create_dataset(
        "collection_normal", "0001", "sample_normal", now, dataset_duration
    )
    _store_dataset(client, dataset)
    messages.get(timeout=10)

    now += dataset_duration + deadtime

    # Directory is empty when the file count is calculated (on the ICAT server side)
    dataset = exp_session.create_dataset(
        "collection_missing", "0002", "sample_missing", now, dataset_duration
    )
    with _temporary_empty_directory(dataset.path):
        _store_dataset(client, dataset)
        messages.get(timeout=10)

    return exp_session


@contextmanager
def _temporary_empty_directory(directory_path: str) -> Generator[None, None, None]:
    original_name = path_utils.basename(directory_path)
    new_name = f"{original_name}_tmp"
    renamed_path = os.path.join(path_utils.dirname(directory_path), new_name)
    try:
        os.rename(directory_path, renamed_path)
        os.makedirs(directory_path)
        yield
    finally:
        os.rmdir(directory_path)
        os.rename(renamed_path, directory_path)


def _start_investigation(
    client: IcatClient,
    messages,
    root_dir: str,
    proposal: str,
    beamline: str,
    startdate: datetime.datetime,
    enddate: datetime.datetime,
) -> _ExperimentalSession:
    session = startdate.strftime("%Y%m%d")
    session_dir = path_utils.markdir(
        os.path.join(root_dir, proposal, beamline, session)
    )
    raw_root_dir = path_utils.markdir(os.path.join(session_dir, "RAW_DATA"))
    os.makedirs(raw_root_dir, exist_ok=True)

    exp_session = _ExperimentalSession(
        session_dir=session_dir,
        raw_root_dir=raw_root_dir,
        raw_data_format="esrfv3",
        proposal=proposal,
        beamline=beamline,
        session=session,
        startdate=startdate.date(),
        search_url=f"https://data.esrf.fr/beamline/{beamline}?search={proposal}",
        datasets=dict(),
    )

    client.start_investigation(
        beamline=beamline,
        proposal=proposal,
        start_datetime=startdate,
        end_datetime=enddate,
    )
    messages.get(timeout=10)

    exp_session.icat_investigation = sync_types.IcatInvestigation(
        id=0,
        url="https://data.esrf.fr/investigation/0/datasets",
        search_url=f"https://data.esrf.fr/beamline/{beamline}?search={proposal}",
        registered_datasets=list(),
        startdate=startdate,
        enddate=enddate,
    )

    return exp_session


def _store_dataset(client: IcatClient, dataset: sync_types.Dataset) -> None:
    client.store_dataset(
        beamline=dataset.beamline,
        proposal=dataset.proposal,
        dataset=dataset.name,
        path=dataset.path,
        metadata=dataset.metadata,
    )


def _chunk_duration(
    startdate: datetime.datetime,
    enddate: datetime.datetime,
    nchunks: int,
    deadtime: int = 0,
):
    deadtime = datetime.timedelta(seconds=deadtime)
    total_deadtime = (nchunks + 1) * deadtime
    total_time = enddate - startdate
    chunk_duration = (total_time - total_deadtime) / nchunks
    now = startdate + deadtime
    return now, chunk_duration, deadtime


def _save_scan(
    nxroot: h5py.File,
    startdate: datetime.datetime,
    duration: datetime.timedelta,
    sample_name: str,
    failed: bool = False,
) -> Optional[datetime.datetime]:
    scans = list(nxroot)
    if scans:
        scan = max(map(int, map(float, scans))) + 1
    else:
        scan = 1
    name = f"{scan}.1"
    grp = nxroot.create_group(name)

    grp["start_time"] = startdate.isoformat()
    if failed:
        return

    enddate = startdate + duration
    grp["end_time"] = enddate.isoformat()
    grp["sample/name"] = sample_name
    return enddate


@contextmanager
def _init_dataset(
    raw_root_dir: str,
    collection: str,
    dataset: str,
) -> Generator[h5py.File, None, None]:
    basename = f"{collection}_{dataset}"
    dataset_dir = os.path.join(raw_root_dir, collection, basename)
    os.makedirs(dataset_dir, exist_ok=True)

    filename = os.path.basename(dataset_dir) + ".h5"
    with h5py.File(os.path.join(dataset_dir, filename), mode="w") as f:
        yield f


def _as_datetime(isoformat: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(isoformat).astimezone()


def _astimezone_string(tzinfo, iso: bool = True) -> Callable[[datetime.datetime], str]:
    def astimezone_string(dt: datetime.datetime) -> str:
        dt = dt.astimezone(tz=tzinfo)
        if iso:
            return dt.isoformat()
        return str(dt)

    return astimezone_string
