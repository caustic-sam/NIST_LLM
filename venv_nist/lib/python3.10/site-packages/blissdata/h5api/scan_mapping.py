import time
import textwrap
import logging
from copy import copy
from collections.abc import Mapping
from pathlib import PurePosixPath as Path
from blissdata.redis_engine.scan import ScanState

"""An H5 scan mapping is a tree composed of group, dataset or link nodes.
The following node classes allows to either load an existing mapping or to
create one, eg:

    # CREATE MAPPING
    >>> root = GroupNode(root=True, attrs={'toto': 42})
    >>> root["subgroup"] = GroupNode(attrs={"hello": "world"})
    >>> root["subgroup"]["dset1"] = DatasetNode(value="string_value", attrs={"foo": "bar"})
    >>> root["subgroup"]["dset2"] = DatasetNode(stream="stream_name")
    >>> root["link"] = SoftLinkNode(target="/subgroup/dset2")
    >>> print(root)
    /
    ├──subgroup
    │  ├──dset1
    │  └──dset2
    └──link

    mapping_dict = root.dump()

    # serialize into json
    # transmit
    # deserialize json

    # LOAD MAPPING
    >>> root = MappingNode.factory(mapping_dict)
    >>> print(root)
    /
    ├──subgroup
    │  ├──dset1
    │  └──dset2
    └──link

IMPORTANT: A mapping node is not an H5-like object, it helps parsing the
mapping grammar. Then we can implement H5-like groups and datasets on
top of it.
"""

_logger = logging.getLogger(__name__)


class MappingNode:
    def __init__(self):
        """A node's parent and name are set by the group which receives it,
        this way it can't be inserted at multiple places and its path stays
        up to date. See GroupNode's __getitem__ and __delitem__ for details."""
        self._parent = None
        self._name = None

    @property
    def parent(self):
        return self._parent

    @property
    def name(self):
        return self._name

    @property
    def path(self):
        if self.name == "/":
            return self.name
        elif None in [self.parent, self.name]:
            raise Exception("path is undefined")
        else:
            return str(Path(self.parent.path) / self.name)

    def factory(raw: dict) -> "MappingNode":
        node = MappingNode._recursive_factory(raw, "/")
        node._name = "/"
        return node

    def _recursive_factory(raw: dict, path: str) -> "MappingNode":
        type = raw["type"]
        if type == "group":
            group = GroupNode(attrs=raw.get("attrs"))
            children = raw.get("items", {})
            for name, child in children.items():
                child_path = str(Path(path) / name)
                try:
                    group[name] = MappingNode._recursive_factory(child, child_path)
                except Exception as e:
                    # skip node, but keep parsing the rest of the tree
                    _logger.warning(f"Unable to load h5 mapping '{child_path}': {e}")
            return group
        elif type == "dset":
            return DatasetNode(
                value=raw.get("value"), stream=raw.get("stream"), attrs=raw.get("attrs")
            )
        elif type == "softlink":
            return SoftLinkNode(raw["target"])
        else:
            raise ValueError(f"Unknown node type '{type}'")


class DatasetNode(MappingNode):
    def __init__(self, attrs=None, value=None, stream=None):
        super().__init__()
        self.value = value
        self.stream: str = stream
        self.attrs: dict = {} if attrs is None else attrs

    def __copy__(self):
        # ignore ._parent and ._name so the copy is not part of any tree
        return DatasetNode(
            attrs=copy(self.attrs), value=copy(self.value), stream=self.stream
        )

    def dump(self) -> dict:
        ret = {"type": "dset"}
        if self.attrs:
            ret["attrs"] = self.attrs
        if self.value:
            ret["value"] = self.value
        if self.stream:
            ret["stream"] = self.stream
        return ret


class SoftLinkNode(MappingNode):
    def __init__(self, target: str):
        super().__init__()
        self.target = target

    def dump(self) -> dict:
        return {
            "type": "softlink",
            "target": self.target,
        }


class GroupNode(MappingNode, dict):
    def __init__(self, attrs=None, root=False):
        super().__init__()
        self.attrs = {} if attrs is None else attrs
        if root:
            self._name = "/"

    def dump(self) -> dict:
        ret = {"type": "group"}
        if self.attrs:
            ret["attrs"] = self.attrs
        if self:
            ret["items"] = {k: v.dump() for k, v in self.items()}
        return ret

    def update(self, other):
        self.attrs.update(other.attrs)
        # TODO this is not recursive, it just updates top level
        dict.update(self, other)

    def __copy__(self):
        raise NotImplementedError

    def __delitem__(self, name: str):
        self[name]._parent = None
        self[name]._name = None
        dict.__delitem__(self, name)

    def __setitem__(self, name: str, child: MappingNode):
        if child.parent is not None:
            raise RuntimeError("Node already has parent, can't be in multiple places")
        if name in self:
            del self[name]

        child._parent = self
        child._name = name
        dict.__setitem__(self, name, child)

    def __str__(self):
        """Display GroupNode as a tree:
        /
        └──1.1
           ├──instrument
           │  ├──name
           │  ├──beamstop
           │  │  └──status
        """

        def tree_view_rec(name, node):
            v = name + "\n"
            if isinstance(node, GroupNode):
                items = list(node.items())
            else:
                items = []
            for name, child in items[:-1]:
                text = tree_view_rec(name, child)
                text = textwrap.indent(text, "│  ")
                text = "├──" + text[3:]
                v += text
            if items:
                name, child = items[-1]
                text = tree_view_rec(name, child)
                text = textwrap.indent(text, "   ")
                text = "└──" + text[3:]
                v += text
            return v

        return tree_view_rec(self.name, self)[:-1]


class ScanMapper(Mapping):
    """A ScanMapper takes a blissdata's Scan and allows to walk through its
    H5-mapping tree with __getitem__, for example:

        mapped_scan = ScanMapper(scan) # a scan from blissdata
        node_a = mapped_scan["/15.1/measurement"]
        node_b = mapped_scan["/15.1/instrument/diode"]
        node_c = node_b["data"]

    The scan mapping is kept up-to-date in case the layout evolves during scan
    lifetime.
    """

    def __init__(self, scan):
        self._scan = scan
        self._root = None
        self._last_update = time.perf_counter()

    @property
    def scan(self):
        return self._scan

    def __len__(self):
        entry = self._updated_root()
        return len(entry)

    def __iter__(self):
        entry = self._updated_root()
        yield from entry.keys()

    def __getitem__(self, path: str) -> MappingNode:
        """Return the MappingNode associated to that path.
        Links are not resolved here, instead a SoftLinkNode is returned and it
        is caller choice to resolve it or not.
        """
        assert path.startswith("/")
        entry = self._updated_root()
        current_path = Path("/")
        for part in Path(path).parts:
            if part == "/":
                continue
            current_path /= part
            if not isinstance(entry, GroupNode):
                raise KeyError(str(current_path))
            try:
                entry = entry[part]
            except KeyError:
                raise KeyError(str(current_path))
        return entry

    def _updated_root(self) -> MappingNode:
        """Return a mapping tree loaded from scan info. Reuse previous answer if
        not older than 0.2 seconds (polling Redis to 5Hz at most if fine).
        Moreover, scan state doesn't change that much and the scan's json is
        only downloaded on changes.

        In case the scan provides no mapping, an empty GroupNode is returned.
        """
        scan_state_changed = False

        # Mapping is not expected to be published before scan is prepared, wait
        while self._scan.state < ScanState.PREPARED:
            self._scan.update()
            self._last_update = time.perf_counter()
            scan_state_changed = True

        # Update whenever the scan is not CLOSED and the last_update is not too
        # recent.
        min_update_interval = 0.2
        now = time.perf_counter()
        if (
            self._last_update + min_update_interval < now
            and self._scan.state < ScanState.CLOSED
        ):
            scan_state_changed = self._scan.update(block=False)
            self._last_update = time.perf_counter()

        # (re)generate mapping tree if necessary
        if scan_state_changed or self._root is None:
            try:
                self._root = MappingNode.factory(self._scan.info["mapping"])
            except KeyError:
                _logger.warning(
                    f"Scan {self._scan.number} has no hdf5 mapping ({self._scan.key})"
                )
                return GroupNode(root=True)

        return self._root
