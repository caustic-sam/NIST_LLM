import re
import time
import functools
import numpy as np
from pathlib import PurePosixPath as Path

from blissdata.beacon.data import BeaconData
from blissdata.redis_engine.store import DataStore
from blissdata.h5api import abstract
from blissdata.h5api.scan_mapping import (
    MappingNode,
    GroupNode,
    DatasetNode,
    ScanMapper,
    SoftLinkNode,
)
from blissdata.redis_engine.exceptions import EndOfStream, IndexNotYetThereError


class Attributes(abstract.Attributes):
    def __init__(self, dct):
        self.dct = dct
        super().__init__()

    def __repr__(self):
        return f"<Attributes of Redis-HDF5 object at {hex(id(self))}>"

    def __getitem__(self, key: str):
        return self.dct[key]

    def __iter__(self):
        yield from self.dct

    def __len__(self):
        return len(self.dct)


class File(abstract.File):
    """This File class mimics the h5py API without an actual file, instead it
    exposes data from Redis directly by relying on Scans and Streams. It means
    file can iterate on live data.
    """

    def __init__(self, filepath: str, data_store: DataStore | None = None):
        """If no data_store is provided, but $BEACON_HOST variable is defined,
        then try to query Redis address from that beacon server."""
        self._filepath = Path(filepath)
        self._closed = False
        if data_store is None:
            try:
                beacon = BeaconData()
            except ValueError as e:
                raise ValueError(
                    f"No DataStore provided, but cannot ask beacon server neither: {e}"
                ) from None
            self._data_store = DataStore(beacon.get_redis_data_db())
        else:
            self._data_store = data_store

    @property
    def name(self) -> str:
        return "/"

    @property
    def filename(self) -> str:
        return str(self._filepath)

    @property
    def attrs(self) -> abstract.Attributes:
        return Attributes({"NX_class": "NXroot"})

    @property
    def file(self) -> "File":
        return self

    @property
    def parent(self) -> "Group":
        return self["/"]

    def close(self):
        self._closed = True

    def __repr__(self):
        return f'<{self.__module__}.{type(self).__name__} "{self._filepath.name}">'

    def __len__(self):
        """Return the instant length from the scans that are already there."""
        return len(self.keys())

    def __getitem__(self, path: str):
        scan_path, sub_path = self._split_path(path)
        path = Path("/") / path
        if scan_path == "/":
            return Group(self, path, self.attrs)

        try:
            scan_mapper = self._get_scan_mapper_by_path(scan_path)
            node = scan_mapper[str(path)]
        except KeyError:
            raise KeyError(f"No such path: {path}")

        visited_paths = []
        while isinstance(node, SoftLinkNode):
            visited_paths.append(path)
            path = Path(node.path).parent / node.target
            if path in visited_paths:
                raise KeyError(
                    f"Link can't be resolved, found cyclic links: {[str(p) for p in visited_paths]}"
                )
            try:
                node = scan_mapper[str(path)]
            except KeyError:
                raise KeyError(f"Broken link, no such path: {path}")

        realpath = node.path  # if softlink where encountered
        return self._mapping_node_to_h5(realpath, node, scan_mapper.scan)

    def _mapping_node_to_h5(self, path: str, node: MappingNode, scan):
        if isinstance(node, GroupNode):
            return Group(self, str(path), node.attrs)
        elif isinstance(node, DatasetNode):
            if node.value is not None:
                return StaticDataset(
                    self, str(path), attrs=node.attrs, value=node.value
                )
            elif node.stream is not None:
                return StreamDataset(
                    self, str(path), attrs=node.attrs, stream=scan.streams[node.stream]
                )
            else:
                raise TypeError(f"Unknown dataset type {node}")
        elif isinstance(node, SoftLinkNode):
            raise TypeError("Oops, unresolved SoftLinkNode should not end up there")
        else:
            raise TypeError(f"Unknown node type {type(node).__name__}")

    def __iter__(self) -> str:
        """Iterate forever: keep waiting for new scans once existing ones are
        yielded."""
        # iterate through existing scans in that file_path
        ts, keys = self._data_store.search_existing_scans(path=self.filename)
        for key in keys:
            yield from self._get_scan_mapper_by_key(key)

        # iterate forever over the next scans
        while True:
            ts, key = self._data_store.get_next_scan(since=ts)
            # Note: Read a single json attribute without loading the scan,
            # this requires to know the json model and should not be used
            # outside of blissdata (a proper API should be discussed if needed)
            path = self._data_store._redis.json().get(key, "id.path")
            if path == self.filename:
                yield from self._get_scan_mapper_by_key(key)

    def keys(self):
        """Instant list of keys in the file. Can be used to only iterate over
        existing scans without blocking then."""
        _, keys = self._data_store.search_existing_scans(path=self.filename)
        ret = set()
        for key in keys:
            ret |= set(self._get_scan_mapper_by_key(key).keys())
        return ret

    def _get_scan_mapper_by_path(self, scan_path: str) -> ScanMapper:
        key = self._get_scan_key(scan_path)
        return self._get_scan_mapper_by_key(key)

    def _get_scan_key(self, scan_path: str) -> str:
        return File._get_scan_key_cached(self._data_store, self.filename, scan_path)

    @staticmethod
    @functools.lru_cache(maxsize=100)
    def _get_scan_key_cached(
        data_store: DataStore, filename: str, scan_path: str
    ) -> str:
        if not re.fullmatch("/[0-9]*.[0-9]+", scan_path):
            raise KeyError(f"No such path: {scan_path}")
        scan_number = int(scan_path[1:].split(".")[0])
        _, keys = data_store.search_existing_scans(path=filename, number=scan_number)
        if len(keys) != 1:
            if not keys:
                # WARNING could be in file and not in Redis anymore...
                raise KeyError(f"No such scan number: {scan_number}")
            else:
                raise RuntimeError(
                    f"Found multiple scans number {scan_number} in {filename}: {keys}"
                )
        return keys.pop()

    def _get_scan_mapper_by_key(self, key: str) -> ScanMapper:
        """Load a scan from Redis, wrap it into a ScanMapper and update
        cache"""
        return File._get_scan_mapper_by_key_cached(self._data_store, key)

    @staticmethod
    @functools.lru_cache(maxsize=100)
    def _get_scan_mapper_by_key_cached(data_store: DataStore, key: str) -> ScanMapper:
        scan = data_store.load_scan(key)
        return ScanMapper(scan)

    def _len_group(self, path: str) -> int:
        """Used by Group.__len__ to query length of a particular path (not only
        the root)"""
        scan_path, sub_path = self._split_path(path)
        if scan_path == "/":
            return len(self)
        else:
            # number of items inside a scan
            try:
                scan_mapper = self._get_scan_mapper_by_path(scan_path)
                group_node = scan_mapper[path]
            except KeyError as e:
                raise KeyError(f"No such path: {str(scan_path / e.args[0])}")
            return len(group_node)

    def _iter_group(self, path: str) -> str:
        """Used by Group.__iter__ to iterate through a particular path (not
        only the root)."""
        scan_path, sub_path = self._split_path(path)
        if scan_path == "/":
            # iterate over scans (never stops)
            yield from self
        else:
            # iterate inside a scan
            try:
                scan_mapper = self._get_scan_mapper_by_path(scan_path)
                group_node = scan_mapper[path]
            except KeyError as e:
                raise KeyError(f"No such path: {str(scan_path / e.args[0])}")
            yield from group_node

    def _split_path(self, path: str) -> tuple[str, str]:
        """Make path canonical and split it into scan and subscan levels
        ''             -> '/',      ''
        '123.1'        -> '/123.1', ''
        '//123.1///'   -> '/123.1', ''
        '/123.1/a/b/c' -> '/123.1', 'a/b/c'
        """
        path = Path("/") / path
        if len(path.parts) <= 2:
            return str(path), ""
        else:
            return str(Path(*path.parts[:2])), str(Path(*path.parts[2:]))


class Node(abstract.Node):
    def __init__(
        self,
        file: File,
        path: str,
        attrs: dict,
    ):
        self._file = file
        self._path = path
        self._attrs = attrs

    @property
    def name(self):
        return self._path

    @property
    def attrs(self):
        return Attributes(self._attrs)

    @property
    def file(self):
        return self._file

    @property
    def parent(self):
        parent_path = Path(self._path).parent
        return self._file[str(parent_path)]


class Group(Node, abstract.Group):
    def __init__(
        self,
        file: File,
        path: str,
        attrs: dict,
    ):
        assert path.startswith("/")
        Node.__init__(self, file, path, attrs)

    @property
    def name(self):
        return self._path

    def __repr__(self):
        return f'<{self.__module__}.{type(self).__name__} "{self._path}">'

    def __getitem__(self, path: str):
        try:
            return self._file[str(self._path / Path(path))]
        except TypeError:
            raise TypeError(
                f"Accessing a group is done with bytes or str, not {type(path)}"
            )

    def __iter__(self):
        yield from self._file._iter_group(self._path)

    def __len__(self):
        return self._file._len_group(self._path)


class StaticDataset(Node, abstract.Dataset):
    def __init__(self, file: File, path: str, value, attrs: dict):
        Node.__init__(self, file, path, attrs)
        self._value = value

    def __repr__(self):
        return f'<{self.__module__}.{type(self).__name__} "{self._value}">'

    def __getitem__(self, idx):
        if idx == ():
            return self._value
        return self._value[idx]

    def __len__(self):
        return len(self._value)

    @property
    def dtype(self):
        raise NotImplementedError

    @property
    def shape(self):
        raise NotImplementedError

    @property
    def size(self):
        raise NotImplementedError

    def __iter__(self):
        # TODO if scalar:
        #     raise TypeError("Can't iterate over a scalar dataset")
        yield from self._value  # TODO only if value is iterable

    @property
    def ndim(self):
        raise NotImplementedError


class StreamDataset(Node, abstract.Dataset):
    def __init__(self, file: File, path: str, stream, attrs: dict):
        assert stream.kind == "array"
        Node.__init__(self, file, path, attrs)
        self._stream = stream

    def __getitem__(self, idx):
        if isinstance(idx, int):
            if idx < 0:
                self._stream.wait_seal()
                return self._stream[idx]
            else:
                while True:
                    try:
                        return self._stream[idx]
                    except IndexNotYetThereError:
                        time.sleep(0.5)
        elif isinstance(idx, slice):
            if idx.stop is None or idx.stop < 0:
                self._stream.wait_seal()
                return self._stream[idx]
            else:
                while len(self._stream) < idx.stop and not self._stream.is_sealed():
                    time.sleep(0.5)
                return self._stream[idx]
        elif isinstance(idx, tuple):
            if idx == ():
                return self._stream[:]
            else:
                raise NotImplementedError

    def __len__(self):
        return len(self._stream)

    @property
    def dtype(self):
        return self._stream.dtype

    @property
    def shape(self):
        return (len(self),) + self._stream.shape

    @property
    def size(self):
        return np.prod(self.shape)

    def __iter__(self):
        cursor = self._stream.cursor()
        timeout = 1.0
        while True:
            try:
                view = cursor.read(timeout=timeout)
                yield from view.get_data()
                # TODO views should be iterable to choose on their own what's
                # the best batch size to download (e.g. LimaView)
            except EndOfStream:
                break

    @property
    def ndim(self):
        return len(self.shape)
