import os
import time
from numbers import Number
from datetime import datetime
from contextlib import contextmanager
from multiprocessing import Queue, Process
from collections.abc import Sequence, Iterator, Mapping
from typing import NamedTuple

# multiprocessing.Queue is an alias and can't be used for annotations, see:
# https://github.com/beartype/beartype/issues/397
from multiprocessing.queues import Queue as GenericQueue

import h5py
import numpy
from numpy.typing import DTypeLike
from silx.io.dictdump import nxtodict

SEP = "/"


class DataPoint(NamedTuple):
    values: numpy.ndarray
    url_template: str


class ChannelInfo(NamedTuple):
    name: str
    shape: tuple = tuple()
    dtype: DTypeLike = numpy.uint16
    internal_template: str = os.path.join("{{dirname}}", "{{basename}}") + SEP.join(
        ("::", "{scan_number}.1", "instrument", "{name}")
    )
    external_template: str = os.path.join(
        "{{dirname}}", "scan{scan_number:04d}", "{name}_{file_index:04d}.h5"
    ) + SEP.join(("::", "entry_0000", "ESRF-ID00", "{name}"))
    external: bool = False
    points_per_file: int = 1
    positioner: bool = False
    start_metadata: Mapping = dict()
    end_metadata: Mapping = dict()
    server_delay: Number = 0

    @property
    def ndim(self):
        return len(self.shape)

    def internal_url(self, scan_number: int) -> str:
        url_template = self.internal_template.format(
            scan_number=scan_number, name=self.name
        )
        if self.positioner:
            url_template += f"{SEP}value"
        else:
            url_template += f"{SEP}data"
        return url_template.split("::")[-1]

    def _dataset_url_template(self, scan_number: int, file_index: int):
        if self.external:
            template = self.external_template
        else:
            template = self.internal_template
        url_template = template.format(
            scan_number=scan_number, name=self.name, file_index=file_index
        )
        if self.positioner:
            url_template += f"{SEP}value"
        else:
            url_template += f"{SEP}data"
        return url_template

    def generate(self, scan_number: int) -> Iterator[DataPoint]:
        values = numpy.zeros(self.shape, dtype=self.dtype)
        add = numpy.array(1, dtype=self.dtype)
        if self.points_per_file > 0:
            i = 0
            while True:
                file_index = i // self.points_per_file
                scan_index = i % self.points_per_file
                values = values + add  # copy
                url_template = self._dataset_url_template(scan_number, file_index)
                yield DataPoint(values=values, url_template=url_template)
                i += 1
        else:
            scan_index = 0
            url_template = self._dataset_url_template(scan_number, 0)
            while True:
                values = values + add  # copy
                yield DataPoint(values=values, url_template=url_template)
                scan_index += 1

    def generate_virtual_sources(
        self, npoints: int, scan_number: int
    ) -> Iterator[tuple[str, dict]]:
        if not self.external:
            return
        nfiles = npoints // self.points_per_file
        nlast = npoints % self.points_per_file
        if nlast:
            nfiles += 1
        else:
            nlast = self.points_per_file
        for file_index in range(nfiles):
            url_template = self._dataset_url_template(scan_number, file_index)
            if file_index == (nfiles - 1):
                shape = (nlast,) + self.shape
            else:
                shape = (self.points_per_file,) + self.shape
            yield url_template, {"shape": shape, "dtype": self.dtype}


def timestamp() -> str:
    return datetime.now().astimezone().isoformat()


def ensure_parents(root, dataset: str):
    parts = [s for s in dataset.split(SEP) if s]
    root.attrs.setdefault("NX_class", "NXroot")
    entry = root.require_group(parts[0])
    entry.attrs.setdefault("NX_class", "NXentry")
    parent = entry
    for group_name in parts[1:-1]:
        parent = parent.require_group(group_name)
        parent.attrs.setdefault("NX_class", "NXcollection")
    dset_name = parts[-1]
    return parent, dset_name


def append_data(root, dataset: str, data: numpy.ndarray):
    parent, dset_name = ensure_parents(root, dataset)
    if dset_name in parent:
        dset = parent[dset_name]
        dset.resize(dset.shape[0] + 1, axis=0)
    else:
        dset = parent.create_dataset(
            dset_name,
            shape=(1,) + data.shape,
            dtype=data.dtype,
            maxshape=(None,) + data.shape,
        )
    dset[-1] = data


def save_vds(root, dataset: str, layout: h5py.VirtualLayout):
    parent, dset_name = ensure_parents(root, dataset)
    parent.create_virtual_dataset(dset_name, layout, fillvalue=numpy.nan)


def scan(
    filename: str,
    scan_number: int,
    counters: Sequence[ChannelInfo],
    start_positioners: Mapping | None = None,
    end_positioners: Mapping | None = None,
    start_metadata: Mapping | None = None,
    end_metadata: Mapping | None = None,
    exposure_time: int = 0.1,
    flush_period: int = 0.1,
    npoints: int = 10,
    start_delay: Number = 0,
    queue: GenericQueue | None = None,
):
    time.sleep(start_delay)
    print("\nStart writing scan", scan_number)
    dirname = os.path.dirname(filename)
    basename = os.path.basename(filename)
    os.makedirs(dirname, exist_ok=True)

    with lima_servers(counters) as lima_queues:
        with writer_context(filename, scan_number) as entry:
            instrument = entry.create_group("instrument")
            instrument.attrs["NX_class"] = "NXinstrument"
            measurement = entry.create_group("measurement")
            measurement.attrs["NX_class"] = "NXcollection"
            positioners = instrument.create_group("positioners")
            positioners.attrs["NX_class"] = "NXcollection"

            # Save start metadata
            if start_metadata:
                nxtodict(entry, start_metadata, update_mode="add")
            for ctr in counters:
                if ctr.start_metadata:
                    nxtodict(entry, ctr.start_metadata, update_mode="add")

            if start_positioners:
                g = instrument.create_group("start_positioner")
                g.attrs["NX_class"] = "NXcollection"
                for k, v in start_positioners.items():
                    g[k] = v
                    if not any(ctr.name == k for ctr in counters):
                        positioners[k] = v

            entry[f"writer{SEP}status"][()] = "RUNNING"

            # Save data
            generators = [ctr.generate(scan_number) for ctr in counters]
            t0 = time.time()
            for scan_index, *detectors in zip(range(npoints), *generators):
                time.sleep(exposure_time)

                for ctr, data in zip(counters, detectors):
                    url = data.url_template.format(dirname=dirname, basename=basename)
                    destfilename, path_in_file = url.split("::")
                    if destfilename == filename:
                        append_data(entry.parent, path_in_file, data.values)
                        if scan_index == 0:
                            measurement[ctr.name] = h5py.SoftLink(path_in_file)
                    else:
                        lima_queues[ctr.name].put((destfilename, path_in_file, data))

                t1 = time.time()
                if (t1 - t0) >= flush_period:
                    entry.file.flush()
                    t0 = t1

                msg = f"Written point {scan_index} of scan {scan_number}"
                print(msg)
                if queue is not None:
                    queue.put(
                        (
                            scan_number,
                            scan_index,
                            "w",
                        )
                    )

            # Create Lima virtual datasets and soft links
            for ctr in counters:
                if not ctr.external:
                    continue

                layout = h5py.VirtualLayout(
                    shape=(npoints,) + ctr.shape, dtype=ctr.dtype
                )
                off = 0
                for url_template, kwargs in ctr.generate_virtual_sources(
                    npoints, scan_number
                ):
                    url = url_template.format(dirname=dirname, basename=basename)
                    destfilename, path_in_file = url.split("::")
                    n = kwargs["shape"][0]
                    layout[off : off + n] = h5py.VirtualSource(
                        destfilename, path_in_file, **kwargs
                    )
                    off += n

                url_template = ctr.internal_url(scan_number)
                path_in_file = url_template.format(dirname=dirname, basename=basename)
                save_vds(entry.parent, path_in_file, layout)
                measurement[ctr.name] = h5py.SoftLink(path_in_file)

            # Save final metadata
            if end_positioners:
                g = instrument.create_group("end_positioner")
                g.attrs["NX_class"] = "NXcollection"
                for k, v in end_positioners.items():
                    g[k] = v

            if end_metadata:
                nxtodict(entry, end_metadata, update_mode="modify")
            for ctr in counters:
                if ctr.end_metadata:
                    nxtodict(entry, ctr.end_metadata, update_mode="modify")
        print("Finishing writing scan", scan_number)
    print("Finished writing scan", scan_number)


@contextmanager
def writer_context(filename: str, scan_number: int) -> Iterator[h5py.Group]:
    with h5py.File(filename, mode="a") as root:
        root.attrs["NX_class"] = "NXroot"
        entry = root.create_group(f"{scan_number}.1")

        try:
            entry.attrs["NX_class"] = "NXentry"
            entry["start_time"] = timestamp()
            writer = entry.create_group("writer")
            writer.attrs["NX_class"] = "NXnote"
            writer["status"] = "STARTING"
            yield entry
        except Exception:
            writer["status"][()] = "FAILED"
            raise
        else:
            writer["status"][()] = "SUCCEEDED"
        finally:
            entry["end_time"] = timestamp()


@contextmanager
def lima_servers(counters: Sequence[ChannelInfo]):
    queues = dict()
    processed = list()
    for ctr in counters:
        if not ctr.external:
            continue
        queue = Queue()
        process = Process(
            target=lima_main, args=(ctr.name, queue), kwargs={"delay": ctr.server_delay}
        )
        process.start()
        processed.append(process)
        queues[ctr.name] = queue

    try:
        yield queues
    finally:
        for queue in queues.values():
            queue.put(None)
        for process in processed:
            process.join()


def lima_main(name: str, queue: GenericQueue, delay: Number = 0):
    aroot = None
    afilename = None
    print("Start lima server", name)
    try:
        while True:
            job = queue.get()
            if job is None:
                return

            time.sleep(delay)

            filename, path_in_file, data = job
            if afilename != filename:
                if aroot is not None:
                    aroot.close()
                os.makedirs(os.path.dirname(filename), exist_ok=True)
                aroot = h5py.File(filename, "a")

            append_data(aroot, path_in_file, data.values)
    except BaseException as e:
        print("Lima server", name, "failed:", str(e))
        raise
    finally:
        if aroot is not None:
            aroot.close()
        print("Stop lima server", name)


def scans(
    filename: str, scan_numbers: Sequence[int], counters: Sequence[ChannelInfo], **kw
):
    for scan_number in scan_numbers:
        scan(filename, scan_number, counters, **kw)


def log_point(
    scan_number: int,
    scan_index: int,
    queue: GenericQueue | None = None,
    write: bool = True,
):
    if write:
        action = "Written"
    else:
        action = "Read"
    msg = f"{action} point {scan_index} of scan {scan_number}"
    print(msg)
    if queue is not None:
        queue.put((scan_number, scan_index, action[0].lower()))


@contextmanager
def start_scan(
    filename: str, scan_number: int, counters: Sequence[ChannelInfo], **kwargs
) -> GenericQueue:
    queue = Queue()
    kwargs["queue"] = queue
    process = Process(
        target=scan, args=(filename, scan_number, counters), kwargs=kwargs
    )
    process.start()
    try:
        yield queue
    finally:
        process.join()


@contextmanager
def start_scans(
    filename: str,
    scan_numbers: Sequence[int],
    counters: Sequence[ChannelInfo],
    **kwargs,
) -> GenericQueue:
    queue = Queue()
    kwargs["queue"] = queue
    process = Process(
        target=scans, args=(filename, scan_numbers, counters), kwargs=kwargs
    )
    process.start()
    try:
        yield queue
    finally:
        process.join()
