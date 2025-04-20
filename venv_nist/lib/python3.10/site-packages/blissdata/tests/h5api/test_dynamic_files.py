import os
import numpy
import pytest
from blissdata.h5api import dynamic_hdf5
from . import scanner


@pytest.mark.parametrize("server_delay", [0, 0.2], ids=["nodelay", "delay"])
@pytest.mark.parametrize(
    "prioritize_non_native_h5items", [False, True], ids=["native", "nonnative"]
)
def test_single_scan_iteration(tmp_path, server_delay, prioritize_non_native_h5items):
    scan_number = 10
    exposure_time = 0.1
    npoints = 21
    retry_period = exposure_time
    flush_period = 2 * exposure_time

    retry_timeout = 10

    basename = "sample_0001.h5"
    filename = str(tmp_path / basename)
    counters = [
        scanner.ChannelInfo("samy", positioner=True),
        scanner.ChannelInfo("diode1"),
        scanner.ChannelInfo("mca1", shape=(10,)),
        scanner.ChannelInfo(
            "lima1",
            shape=(4, 7),
            external=True,
            points_per_file=2,
            server_delay=server_delay,
        ),
    ]

    with scanner.start_scan(
        filename,
        scan_number,
        counters,
        exposure_time=exposure_time,
        flush_period=flush_period,
        npoints=npoints,
        start_delay=0.5,
    ) as queue:
        with dynamic_hdf5.File(
            filename,
            lima_names=["lima1"],
            retry_timeout=retry_timeout,
            retry_period=retry_period,
            lima_url_template_args={"instrument_name": "ESRF-ID00"},
            prioritize_non_native_h5items=prioritize_non_native_h5items,
        ) as nxroot:
            read_scan_data(nxroot, scan_number, counters, queue)
            with pytest.raises(KeyError):
                nxroot["non_existent"]
            nxentry = nxroot["10.1"]
            with pytest.raises(KeyError):
                nxentry["non_existent"]
    assert_read_write_order(queue, npoints, 1)


@pytest.mark.parametrize(
    "prioritize_non_native_h5items", [False, True], ids=["native", "nonnative"]
)
def test_multiple_scan_iteration(tmp_path, prioritize_non_native_h5items):
    scan_numbers = [10, 11, 12]
    exposure_time = 0.1
    npoints = 21
    retry_period = exposure_time / 4
    flush_period = 2 * exposure_time

    retry_timeout = 10

    basename = "sample_0001.h5"
    filename = str(tmp_path / basename)
    counters = [
        scanner.ChannelInfo("samy", positioner=True),
        scanner.ChannelInfo("diode1"),
        scanner.ChannelInfo("mca1", shape=(10,)),
        scanner.ChannelInfo(
            "lima1", shape=(4, 7), external=True, points_per_file=2, server_delay=0.1
        ),
    ]

    scan_number = 0
    with scanner.start_scans(
        filename,
        scan_numbers,
        counters,
        exposure_time=exposure_time,
        flush_period=flush_period,
        npoints=npoints,
    ) as queue:
        with dynamic_hdf5.File(
            filename,
            lima_names=["lima1"],
            retry_timeout=retry_timeout,
            retry_period=retry_period,
            lima_url_template_args={"instrument_name": "ESRF-ID00"},
            prioritize_non_native_h5items=prioritize_non_native_h5items,
        ) as nxroot:
            for scan in nxroot:  # loops until retry_timeout
                scan_number = int(scan.split(".")[0])
                read_scan_data(nxroot, scan_number, counters, queue)
                if scan_number == scan_numbers[-1]:
                    break
    assert scan_number == scan_numbers[-1]
    assert_read_write_order(queue, npoints, 3)


@pytest.mark.parametrize(
    "prioritize_non_native_h5items", [False, True], ids=["native", "nonnative"]
)
def test_multiple_scan_instrument_list(tmp_path, prioritize_non_native_h5items):
    scan_numbers = [10, 11, 12]
    exposure_time = 0.02
    npoints = 11
    retry_period = exposure_time
    flush_period = 2 * exposure_time

    retry_timeout = 10

    basename = "sample_0001.h5"
    filename = str(tmp_path / basename)
    counters = [
        scanner.ChannelInfo("samy", positioner=True),
        scanner.ChannelInfo("diode1"),
        scanner.ChannelInfo("mca1", shape=(10,)),
        scanner.ChannelInfo("lima1", shape=(4, 7), external=True, points_per_file=2),
    ]
    expected = {ctr.name for ctr in counters if not ctr.external}
    expected.add("positioners")

    if os.name == "nt":
        # Parallel reading does not work on windows
        # This means when the reader has access, the lima VDS is already created
        expected.add("lima1")

    scan_number = 0
    with scanner.start_scans(
        filename,
        scan_numbers,
        counters,
        exposure_time=exposure_time,
        flush_period=flush_period,
        npoints=npoints,
    ):
        with dynamic_hdf5.File(
            filename,
            lima_names=["lima1"],
            retry_timeout=retry_timeout,
            retry_period=retry_period,
            lima_url_template_args={"instrument_name": "ESRF-ID00"},
            prioritize_non_native_h5items=prioritize_non_native_h5items,
        ) as nxroot:
            for scan in nxroot:  # loops until retry_timeout
                scan_number = int(scan.split(".")[0])
                nxentry = nxroot[scan]
                keys = set(nxentry["instrument"])
                assert keys == expected
                if scan_number == 12:
                    break
    assert scan_number == 12


@pytest.mark.parametrize(
    "prioritize_non_native_h5items", [False, True], ids=["native", "nonnative"]
)
def test_single_scan_vds_delay(tmp_path, prioritize_non_native_h5items):
    scan_number = 10
    exposure_time = 0.1
    npoints = 1
    retry_period = exposure_time
    flush_period = 2 * exposure_time

    retry_timeout = 10
    server_delay = 1

    basename = "sample_0001.h5"
    filename = str(tmp_path / basename)
    counters = [
        scanner.ChannelInfo("samy", positioner=True),
        scanner.ChannelInfo("diode1"),
        scanner.ChannelInfo("mca1", shape=(10,)),
        scanner.ChannelInfo(
            "lima1",
            shape=(4, 7),
            external=True,
            points_per_file=1,
            server_delay=server_delay,
        ),
    ]

    with scanner.start_scan(
        filename,
        scan_number,
        counters,
        exposure_time=exposure_time,
        flush_period=flush_period,
        npoints=npoints,
        start_delay=0.5,
    ) as queue:
        with dynamic_hdf5.File(
            filename,
            lima_names=["lima1"],
            retry_timeout=retry_timeout,
            retry_period=retry_period,
            lima_url_template_args={"instrument_name": "ESRF-ID00"},
            prioritize_non_native_h5items=prioritize_non_native_h5items,
        ) as nxroot:
            keys = set(nxroot["10.1/instrument"])
            assert "lima1" in keys
            read_scan_data(nxroot, scan_number, counters, queue)


@pytest.mark.parametrize(
    "prioritize_non_native_h5items", [False, True], ids=["native", "nonnative"]
)
def test_single_scan_slice(tmp_path, prioritize_non_native_h5items):
    npoints = 21
    retry_timeout = 10

    basename = "sample_0001.h5"
    filename = str(tmp_path / basename)
    counters = [
        scanner.ChannelInfo("samy", positioner=True),
        scanner.ChannelInfo("diode1"),
        scanner.ChannelInfo("mca1", shape=(10,)),
        scanner.ChannelInfo("lima1", shape=(4, 7), external=True, points_per_file=2),
    ]

    # Test slicing along the scan dimension
    scan_number = 1
    with scanner.start_scan(filename, scan_number, counters, npoints=npoints) as queue:
        with dynamic_hdf5.File(
            filename,
            lima_names=["lima1"],
            retry_timeout=retry_timeout,
            lima_url_template_args={"instrument_name": "ESRF-ID00"},
            prioritize_non_native_h5items=prioritize_non_native_h5items,
        ) as nxroot:
            for i, ctr in enumerate(counters):
                dataset = nxroot[ctr.internal_url(scan_number)]
                for scan_index, data in enumerate(dataset[5:8], 5):
                    expected = numpy.full(ctr.shape, scan_index + 1, dtype=ctr.dtype)
                    numpy.testing.assert_array_equal(data, expected)
                if i == 0 and os.name != "nt":
                    # Parallel reading does not work on windows
                    assert (
                        queue.qsize() < npoints
                    ), "slicing should return before the writing finished"

    scan_number += 1
    with scanner.start_scan(filename, scan_number, counters, npoints=npoints) as queue:
        with dynamic_hdf5.File(
            filename,
            lima_names=["lima1"],
            retry_timeout=retry_timeout,
            lima_url_template_args={"instrument_name": "ESRF-ID00"},
            prioritize_non_native_h5items=prioritize_non_native_h5items,
        ) as nxroot:
            for i, ctr in enumerate(counters):
                dataset = nxroot[ctr.internal_url(scan_number)]
                for scan_index, data in enumerate(dataset[[5, 6, 7]], 5):
                    expected = numpy.full(ctr.shape, scan_index + 1, dtype=ctr.dtype)
                    numpy.testing.assert_array_equal(data, expected)
                if i == 0 and os.name != "nt":
                    # Parallel reading does not work on windows
                    assert (
                        queue.qsize() < npoints
                    ), "slicing should return before the writing finished"

    # Test slicing along the scan and detector dimensions
    scan_number += 1
    with scanner.start_scan(filename, scan_number, counters, npoints=npoints) as queue:
        with dynamic_hdf5.File(
            filename,
            lima_names=["lima1"],
            retry_timeout=retry_timeout,
            lima_url_template_args={"instrument_name": "ESRF-ID00"},
            prioritize_non_native_h5items=prioritize_non_native_h5items,
        ) as nxroot:
            for i, ctr in enumerate(counters):
                dataset = nxroot[ctr.internal_url(scan_number)]
                idx = (slice(5, 8),) + ctr.ndim * (0,)
                for scan_index, data in enumerate(dataset[idx], 5):
                    assert data == scan_index + 1
                if i == 0 and os.name != "nt":
                    # Parallel reading does not work on windows
                    assert (
                        queue.qsize() < npoints
                    ), "slicing should return before the writing finished"


def assert_read_write_order(queue, npoints, nscans):
    # Check whether all points are written and read
    lst = list()
    while not queue.empty():
        lst.append(queue.get())
    nexpected = 2 * npoints * nscans
    assert len(lst) == nexpected

    # Check whether reading and writing happened in parallel
    if os.name == "nt":
        # Parallel reading does not work on windows
        return

    actions = dict()
    for scan_number, _, action in lst:
        scan_actions = actions.setdefault(scan_number, list())
        scan_actions.append(action)

    for scan_actions in actions.values():
        gaps = numpy.diff([i for i, action in enumerate(scan_actions) if action == "r"])
        assert (gaps > 1).any(), str(lst)


def read_scan_data(nxroot, scan_number, counters, queue):
    print(f"Start reading scan {scan_number}")
    datasets = [nxroot[ctr.internal_url(scan_number)] for ctr in counters]
    for scan_index, points in enumerate(zip(*datasets)):
        assert len(points) == len(counters)
        for ctr, data in zip(counters, points):
            expected = numpy.full(ctr.shape, scan_index + 1, dtype=ctr.dtype)
            numpy.testing.assert_array_equal(data, expected)
        scanner.log_point(scan_number, scan_index, queue, write=False)
    print(f"Finished reading scan {scan_number}")
