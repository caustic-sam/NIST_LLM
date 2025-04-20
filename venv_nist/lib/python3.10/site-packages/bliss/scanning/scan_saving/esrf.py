from __future__ import annotations

import re
import os
import time
import enum
import datetime
from typing import Any, Optional
from collections.abc import Iterable, Generator
from contextlib import contextmanager

from .template_store import Field
from .template_store import FieldLocation
from .base import BasicScanSaving

import gevent.lock

from bliss import is_bliss_shell
from bliss import current_session
from bliss.common import logtools
from bliss.config.static import get_config
from bliss.config.static import ConfigNode
from bliss.config.settings import scan as scan_redis

from bliss.icat.dataset import Dataset
from bliss.icat.proposal import Proposal
from bliss.icat.dataset_collection import DatasetCollection
from bliss.icat.client import is_null_client
from bliss.icat.client import DatasetId
from bliss.icat.client import IcatClientInterface
from bliss.icat.client import icat_client_config
from bliss.icat.client import icat_client_from_config
from bliss.icat.json_policy import RedisJsonTree
from bliss.icat.json_policy import RedisJsonNode
from flint.client.proxy import restart_flint


class ESRFDataPolicyEvent(enum.Enum):
    Enable = "enabled"
    Disable = "disabled"
    Change = "changed"


_DATAPOLICY_NODE_LOCK = gevent.lock.BoundedSemaphore()


class ESRFScanSaving(BasicScanSaving):
    """Parameterized representation of the scan data file path
    according to the ESRF data policy

    .. code::

        <base_path>/<template>/<data_filename><file_extension>

    where

     - <base_path> depends on the proposal type
     - <template> is a fixed template
     - <data_filename> is a fixed template
     - <file_extension> depends on the select writer
    """

    class _Fields:
        #
        # ESRF data policy fields
        #
        _proposal = Field()
        _ESRFScanSaving__proposal_timestamp = Field(
            default=0.0,
            data_type=float,
        )
        _proposal_session = Field()
        _collection = Field()
        _mount = Field()
        _reserved_dataset = Field()
        _dataset = Field()
        #
        # Override BasicScanSaving fields
        #
        data_policy = Field(
            location=FieldLocation.local,
            mutable=False,
            default="ESRF",
        )
        base_path = Field(
            location=FieldLocation.attribute,
            mutable=False,
        )
        data_filename = Field(
            location=FieldLocation.local,
            mutable=False,
            default="{collection_name}_{dataset_name}",
        )
        template = Field(
            location=FieldLocation.attribute,
            mutable=False,
        )
        _writer_module = Field(
            default="nexus",
        )
        images_path_relative = Field(
            location=FieldLocation.local,
            mutable=False,
            default=True,
            data_type=bool,
        )
        #
        # New fields
        #
        dataset_number_format = Field(
            default="%04d",
        )
        proposal_name = Field(
            location=FieldLocation.attribute,
            init=False,
        )
        proposal_type = Field(
            location=FieldLocation.attribute,
            mutable=False,
        )
        beamline = Field(
            location=FieldLocation.attribute,
            mutable=False,
        )
        proposal_dirname = Field(
            location=FieldLocation.attribute,
            mutable=False,
        )
        proposal_session_name = Field(
            location=FieldLocation.attribute,
            init=False,
        )
        mount_point = Field(
            location=FieldLocation.attribute,
            init=False,
        )
        collection_name = Field(
            location=FieldLocation.attribute,
            init=False,
        )
        dataset_name = Field(
            location=FieldLocation.attribute,
            init=False,
        )
        _proposal_session_name_format = Field(
            location=FieldLocation.local,
            default="%Y%m%d",
            mutable=False,
        )

    _LEGACY_WARDROBE_TEMPLATE = "esrf_scan_saving:{}"
    _REDIS_KEY_TEMPLATE = "scan_saving:esrf:{}"

    def __init__(self, name=None, session_name=None, **kwargs):
        self._icat_client = None
        self._proposal_object = None
        self._collection_object = None
        self._dataset_object = None
        self._data_policy_changing = False
        super().__init__(name, session_name, **kwargs)

    def __dir__(self) -> Iterable[str]:
        # For autocompletion
        lst = list(super().__dir__())
        lst += [
            "proposal",
            "collection",
            "dataset",
            "icat_info",
            "icat_register_datasets",
        ]
        return lst

    def _extra_info(self) -> list[tuple[str]]:
        lst = super()._extra_info()
        proposal_name = self.proposal_name
        beamline = self.beamline
        investigation_summary = self.icat_client.investigation_summary(
            beamline=beamline, proposal=proposal_name
        )
        for name, value in investigation_summary:
            lst.append(["ICAT", name, value])
        confirmed, unconfirmed = self.icat_confirm_datasets()
        if confirmed is None:
            lst.append(
                ["ICAT", "datasets", f"{len(unconfirmed)} unconfirmed, ??? confirmed"]
            )
        else:
            lst.append(
                [
                    "ICAT",
                    "datasets",
                    f"{len(unconfirmed)} unconfirmed, {len(confirmed)} confirmed",
                ]
            )
        return lst

    ############################
    ### BEACON CONFIGURATION ###
    ############################

    @property
    def scan_saving_config(self) -> dict[str, Any]:
        if self._testing:
            return {
                "beamline": "id00",
                "tmp_data_root": "/tmp/data/tmp",
                "visitor_data_root": "/tmp/data/visitor",
                "inhouse_data_root": "/tmp/data/inhouse",
            }
        config = self._scan_saving_config_node
        if config is None:
            return dict()
        return config.to_dict()

    @property
    def _scan_saving_config_node(self) -> Optional[ConfigNode]:
        """Session config or an empty dictionary, if there is no associated session."""
        if self._testing:
            return
        node = self._session_config_node
        if node is not None:
            node = node.get("scan_saving")
            if node is not None:
                return node
        node = self._root_config_node
        if node is not None:
            return node.get("scan_saving")

    @property
    def _session_config_node(self) -> Optional[ConfigNode]:
        """Session config or an empty dictionary, if there is no associated session."""
        if self._testing:
            return
        session_name = self._session_name
        if session_name:
            return get_config().get_config(session_name)

    @property
    def _root_config_node(self) -> Optional[ConfigNode]:
        """Static config root"""
        if self._testing:
            return None
        return get_config().root

    ######################
    ### DATA FILE PATH ###
    ######################

    @property
    def template(self) -> str:
        version = self.scan_saving_config.get("directory_structure_version", 3)
        if version == 1:
            return "{proposal_dirname}/{beamline}/{proposal_session_name}/{collection_name}/{collection_name}_{dataset_name}"
        if version == 2:
            return "{proposal_dirname}/{beamline}/{proposal_session_name}/raw/{collection_name}/{collection_name}_{dataset_name}"
        if version == 3:
            return "{proposal_dirname}/{beamline}/{proposal_session_name}/RAW_DATA/{collection_name}/{collection_name}_{dataset_name}"
        raise RuntimeError(
            "The scan saving key 'directory_structure_version' from the beamline configuration must be either 1, 2 or 3."
        )

    @property
    def base_path(self) -> str:
        """Root directory depending in the proposal type (inhouse, visitor, tmp)"""
        return self._get_base_path_template(icat=False)

    def _get_base_path_template(self, icat: bool = False) -> str:
        """Root directory depending in the proposal type (inhouse, visitor, tmp)"""
        default_base_paths = {
            "inhouse": "/data/{beamline}/inhouse",
            "visitor": "/data/visitor",
            "tmp": "/data/{beamline}/tmp",
        }
        ptype = self.proposal_type
        if ptype not in default_base_paths:
            ptype = "tmp"

        base_path = default_base_paths[ptype]
        base_path = self._get_mount_point(f"{ptype}_data_root", base_path)
        if icat:
            base_path = self._get_mount_point(f"icat_{ptype}_data_root", base_path)
        return base_path

    @property
    def date(self):
        timestamp = self._ESRFScanSaving__proposal_timestamp
        if timestamp:
            tm = datetime.datetime.fromtimestamp(timestamp)
        else:
            tm = datetime.datetime.now()
        return tm.strftime(self.date_format)

    @property
    def mount_points(self) -> set[str]:
        """All mount points of all proposal types"""
        mount_points = set()
        for key in ["inhouse_data_root", "visitor_data_root", "tmp_data_root"]:
            mount_points |= set(self._mount_points_from_config(key, ""))
            mount_points |= set(self._mount_points_from_config(f"icat_{key}", ""))
        return mount_points

    @property
    def mount_point(self) -> str:
        """Current mount point (defines :code:`base_path` selection
        from scan saving configuration) for all proposal types.
        """
        if self._mount is None:
            self._mount = ""
        return self._mount

    @mount_point.setter
    def mount_point(self, value: Optional[str]) -> None:
        """
        :raises ValueError: not in the available mount points
        """
        choices = self.mount_points
        if value is None:
            value = ""
        if value not in choices:
            raise ValueError(f"The only valid mount points are {choices}")
        self._mount = value

    def _get_mount_point(self, key: str, default: str) -> str:
        """Get proposal type's mount point which defines :code:`base_path`."""
        mount_points = self._mount_points_from_config(key, default)
        current_mp = mount_points.get(self.mount_point, None)
        if current_mp is not None:
            return current_mp
        # Take the first mount point when the current one
        # is not defined for this proposal type
        return mount_points[next(iter(mount_points.keys()))]

    def _mount_points_from_config(self, key: str, default: str) -> dict[str, str]:
        """Get all mount points for the proposal type."""
        mount_points = self.scan_saving_config.get(key, default)
        if isinstance(mount_points, str):
            return {"": mount_points}
        return mount_points

    ############
    ### SCAN ###
    ############

    def on_scan_run(self, save: bool) -> None:
        """Called at the start of a scan (in :code:`Scan.run`)"""
        if save:
            # Dataset metadata is only needed when you have actual data.
            self._init_dataset_metadata()

    ####################
    ### DATA POLICY ####
    ####################

    def newproposal(
        self,
        proposal_name: Optional[str],
        session_name: Optional[str] = None,
        prompt: bool = False,
    ) -> None:
        """The proposal will be created in Redis if it does not exist already."""
        self.proposal_name = proposal_name

        if not session_name and prompt and is_bliss_shell():
            from bliss.shell.cli.user_dialog import UserChoice2
            from bliss.shell.standard import show_dialog

            values = self._valid_proposal_session_names()
            if len(values) == 0:
                print("No session available")
                return

            if len(values) > 1:
                formatted_values = list(zip(values, values))
                dlg = UserChoice2(
                    label="Session of proposal " + self.proposal_name,
                    values=formatted_values,
                )
                result = show_dialog([dlg])
                if not result:
                    print("Selection aborted")
                    return
                session_name = result[dlg]
            else:
                session_name = values[0]

        if session_name:
            self.proposal_session_name = session_name

    def newcollection(
        self,
        collection_name: Optional[str],
        sample_name: Optional[str] = None,
        sample_description: Optional[str] = None,
    ) -> None:
        """The dataset collection will be created in Redis if it does not exist already."""
        self.collection_name = collection_name

        if sample_name:
            self.collection.sample_name = sample_name
        if sample_description is not None:
            self.collection.sample_description = sample_description

    def newsample(
        self, collection_name: Optional[str], description: Optional[str] = None
    ) -> None:
        """Same as `newcollection` with sample name equal to the collection name."""
        self.newcollection(
            collection_name, sample_name=collection_name, sample_description=description
        )

    def newdataset(
        self,
        dataset_name: str | int | None,
        description: Optional[str] = None,
        sample_name: Optional[str] = None,
        sample_description: Optional[str] = None,
    ) -> None:
        """The dataset will be created in Redis if it does not exist already.
        Metadata will be gathered if not already done. RuntimeError is raised
        when the dataset is already closed.

        If `newdataset` is not used, the metadata gathering is done at the
        start of the first scan that aves data.
        """
        self.dataset_name = dataset_name

        if sample_name:
            self.dataset.sample_name = sample_name
        if sample_description is not None:
            self.dataset.sample_description = sample_description
        if description is not None:
            self.dataset.description = description

    @property
    def elogbook(self) -> IcatClientInterface:
        return self.icat_client

    def endproposal(self) -> None:
        """Switch to a new proposal with a default name. Close the proposal even when the same proposal."""
        self._set_proposal_name(None, force_reset=True)

    def enddataset(self) -> None:
        """Switch to a new dataset with a default name."""
        self.newdataset(None)

    ###########################
    ### DATA POLICY FIELDS ####
    ###########################

    @property
    def proposal_name(self) -> str:
        if self._proposal is None:
            self.proposal_name = None
        return self._proposal

    @proposal_name.setter
    def proposal_name(self, name: Optional[str]) -> None:
        self._set_proposal_name(name)

    def _set_proposal_name(
        self, name: Optional[str], force_reset: bool = False
    ) -> None:
        if name:
            # Should contain only: alphanumeric, space-like, dash and underscore
            if not re.match(r"^[0-9a-zA-Z_\s\-]+$", name):
                raise ValueError("Proposal name is invalid")
            # Normalize: lowercase alphanumeric
            name = re.sub(r"[^0-9a-z]", "", name.lower())
        else:
            yymm = time.strftime("%y%m")
            name = f"{{beamline}}{yymm}"
        self._validate_path_name(name)

        same_name = self._equal_name(self._proposal, name)
        if same_name and not force_reset:
            return

        with self._data_policy_change() as dpchange:
            self._close_proposal()
            self._proposal = name
            self._ESRFScanSaving__proposal_timestamp = time.time()
            self.proposal_session_name = None

            self.icat_client.start_investigation(
                beamline=self.beamline, proposal=self.proposal_name
            )
            if dpchange:
                dpchange[
                    "message"
                ] = f"Proposal set to '{self.proposal_name}' (session '{self.proposal_session_name}')"

        # TODO: The ICAT client used by Flint is not getting
        # the proposal name from Redis but gets it at startup.
        # So we have to restart Flint at this point.
        restart_flint(creation_allowed=False)

    @property
    def proposal_session_name(self) -> str:
        if self._proposal_session is None:
            self.proposal_session_name = None
        return self._proposal_session

    @proposal_session_name.setter
    def proposal_session_name(self, name: Optional[str]) -> None:
        self._validate_path_name(name)
        proposal_session_names = self._valid_proposal_session_names()
        if name not in proposal_session_names:
            name = self._select_proposal_session_name(proposal_session_names)

        with self._data_policy_change() as dpchange:
            same_name = self._equal_name(self._proposal_session, name)
            self._close_collection()
            self._proposal_session = name
            self.collection_name = None
            if dpchange and not same_name:
                dpchange[
                    "message"
                ] = f"Proposal set to '{self.proposal_name}' (session '{self.proposal_session_name}')"

    @property
    def collection_name(self) -> str:
        if self._collection is None:
            self.collection_name = None
        return self._collection

    @collection_name.setter
    def collection_name(self, name: Optional[str]) -> None:
        self._validate_path_name(name)
        if not name:
            # None, "" or 0
            name = "sample"

        with self._data_policy_change() as dpchange:
            same_name = self._equal_name(self._collection, name)
            self._close_collection()
            self._collection = name
            self.dataset_name = None
            if dpchange and not same_name:
                dpchange[
                    "message"
                ] = f"Dataset collection set to '{self.collection_name}'"

    @property
    def dataset_name(self) -> str:
        return self._dataset

    @dataset_name.setter
    def dataset_name(self, name: str | int | None) -> None:
        self._validate_path_name(name)

        with self._data_policy_change() as dpchange:
            old_dataset = self._dataset
            self._close_dataset()
            self._set_dataset_name(name)
            same_name = self._equal_name(old_dataset, self._dataset)
            if dpchange and not same_name:
                dpchange["message"] = f"Dataset set to '{self.dataset_name}'"

    @property
    def beamline(self) -> str:
        bl = self.scan_saving_config.get("beamline")
        if not bl:
            return "{beamline}"

        # Should contain only: alphanumeric, space-like, dash and underscore
        if not re.match(r"^[0-9a-zA-Z_\s\-]+$", bl):
            raise ValueError("Beamline name is invalid")

        # Normalize: lowercase alphanumeric
        return re.sub(r"[^-0-9a-z]", "", bl.lower())

    @property
    def proposal_type(self) -> str:
        proposal_name = self.proposal_name
        beamline = self.beamline

        if proposal_name.startswith(beamline):
            return "inhouse"

        inhouse_prefixes = self.scan_saving_config.get(
            "inhouse_proposal_prefixes", tuple()
        )
        for proposal_prefix in inhouse_prefixes:
            proposal_prefix = re.sub(r"[^0-9a-z]", "", proposal_prefix.lower())
            if proposal_name.startswith(proposal_prefix):
                return "inhouse"

        default_tmp_prefixes = "tmp", "temp", "test"
        tmp_prefixes = self.scan_saving_config.get(
            "tmp_proposal_prefixes", default_tmp_prefixes
        )
        for proposal_prefix in tmp_prefixes:
            proposal_prefix = re.sub(r"[^0-9a-z]", "", proposal_prefix.lower())
            if proposal_name.startswith(proposal_prefix):
                return "tmp"

        return "visitor"

    @property
    def proposal_dirname(self) -> str:
        dirname = self.proposal_name
        if not dirname.isdigit():
            return dirname

        proposal_type = self.proposal_type
        if proposal_type != "visitor":
            return dirname

        # TID does not allow directories with only digits. Add the beamline
        # letters as prefix (e.g. "BM") and remove leading zeros after the
        # beamline name. For example proposal 02-01234 is saved in /data/visitor/bm021234
        beamline = self.beamline
        tmp = re.sub("[0-9]", "", beamline) + dirname
        if not tmp.startswith(beamline):
            return dirname

        dirname = beamline + str(int(tmp[len(beamline) :]))
        return dirname

    def _validate_path_name(self, value: str | int | None) -> None:
        if isinstance(value, str):
            _check_valid_in_path(self.eval_template(value))

    def _equal_name(self, name1: Optional[str], name2: str) -> bool:
        return (self.eval_template(name1) if name1 else None) == (
            self.eval_template(name2) if name2 else None
        )

    def _valid_proposal_session_names(self) -> list[str]:
        search_path = os.path.join(self.base_path, self.proposal_name, self.beamline)
        if not os.path.isdir(search_path):
            return list()
        return [
            f.name
            for f in os.scandir(search_path)
            if f.is_dir() and bool(_PROPOSAL_SESSION_REGEX.match(f.name))
        ]

    def _select_proposal_session_name(self, proposal_session_names: list[str]) -> str:
        if len(proposal_session_names) == 1:
            # There is only one valid session
            return proposal_session_names[0]

        if not proposal_session_names:
            # Select the default session (first of this month)
            dt = datetime.date.today().replace(day=1)
            return dt.strftime(self._proposal_session_name_format)

        # Select the current session based on the date
        ref_time = datetime.datetime.now()
        ref_time += datetime.timedelta(
            hours=self.scan_saving_config.get("newproposal_now_offset", 0)
        )

        dates = [
            datetime.datetime.strptime(date_str, self._proposal_session_name_format)
            for date_str in proposal_session_names
        ]
        dates = sorted(dates)

        for idx, dt in enumerate(dates):
            if int((dt - ref_time).total_seconds()) > 0:
                # First date after ref_time: the previous one corresponds best with ref_time
                selected = dates[max(0, idx - 1)]
                break
        else:
            # For date after ref_time: the most recent date corresponds best
            selected = dates[-1]

        return selected.strftime(self._proposal_session_name_format)

    def _set_dataset_name(self, name: str | int | None) -> None:
        if not name:
            # None, "" or 0
            name = 0
        if isinstance(name, str) and name.isdigit():
            name = int(name)

        has_prefix = isinstance(name, str)
        if has_prefix:
            # With prefix: for example `name = "area1"` or `name = "area_0001"`
            # To avoid name collisions with existing directories, we may need to add a suffix.
            enforce_dataset_suffix = self.scan_saving_config.get(
                "enforce_dataset_suffix", False
            )
            if enforce_dataset_suffix:
                # The name collision is intentional.
                # Dataset names: "prefix_0001", "prefix_0002", "prefix_0003", ...
                name_pattern = f"{re.escape(name)}_([0-9]+)"
                name_template = f"{name}_{self.dataset_number_format}"
                first_name = name_template % 1
            else:
                # The name collision is NOT intentional.
                # Dataset names: "prefix", "prefix_0002", "prefix_0003", ...
                name_pattern = f"{re.escape(name)}(_([0-9]+))?"
                name_template = f"{name}_{self.dataset_number_format}"
                first_name = name
        else:
            # Without prefix: for example `name = 10`
            # Dataset names: "0001", "0002", ...
            name_pattern = "([0-9]+)"
            name_template = self.dataset_number_format
            idxmin = max(name, 1)  # start from 1
            first_name = name_template % idxmin

        # Dataset directory cannot exist and cannot be reserved by other BLISS sessions.
        # Append and/or increment numerical suffix when needed.
        original_dataset_name = self._dataset
        original_reserved_dataset = self._reserved_dataset
        try:
            self._dataset = "{placeholder}"
            root_path_template = self.root_path

            root_path = root_path_template.format(placeholder="")
            parent_dir = os.path.dirname(root_path)

            # Everything except `name_pattern` must be escaped for the regex template
            unique_substring = "PLACEHOLDER"
            while unique_substring in root_path:
                unique_substring += "_"
            root_path = re.escape(
                root_path_template.format(placeholder=unique_substring)
            )
            root_path = root_path.replace(unique_substring, name_pattern)
            dataset_dir_regex = re.compile(f"^{root_path}$")

            final_name = None
            prev_reserved = None
            while True:
                reserved = self._reserved_datasets()
                if os.path.isdir(parent_dir):
                    lst = [os.path.join(parent_dir, s) for s in os.listdir(parent_dir)]
                    reserved.update(lst)

                if prev_reserved == reserved:
                    assert final_name is not None
                    self._dataset = final_name
                    break

                existing_suffixes: list[Optional[int]] = list()
                for dirname in reserved:
                    match = dataset_dir_regex.match(dirname)
                    if match:
                        idx = match.groups()[-1]
                        if idx is None:
                            existing_suffixes.append(0)
                        else:
                            existing_suffixes.append(int(idx))

                if existing_suffixes:
                    idx = max(existing_suffixes) + 1
                    if has_prefix:
                        if idx == 1:
                            # Skip prefix_0001
                            idx = 2
                    else:
                        idx = max(idx, idxmin)
                    final_name = name_template % idx
                else:
                    final_name = first_name

                self._reserved_dataset = root_path_template.format(
                    placeholder=final_name
                )
                self._push_external_cache()
                prev_reserved = reserved
        except BaseException:
            self._reserved_dataset = original_reserved_dataset
            self._dataset = original_dataset_name
            raise

    def _reserved_datasets(self) -> set[str]:
        """The dataset directories reserved by all sessions,
        whether the directories exist or not.
        """
        if self._testing:
            return set()
        pattern = self._REDIS_KEY_TEMPLATE.format("*")
        self_name = self.name
        reserved = set()
        for key in scan_redis(match=pattern):  # new connection to avoid cache
            name = key.split(":")[2]
            if name == self_name:
                continue
            scan_saving = self.__class__(name)
            reserved.add(scan_saving._reserved_dataset)
        return reserved

    @contextmanager
    def _data_policy_change(self, elogbook: bool = True) -> Generator[dict, None, None]:
        dpchange = None
        if not self._data_policy_changing:
            self._data_policy_changing = True
            dpchange = {"message": None}
        try:
            yield dpchange
        finally:
            if dpchange:
                self._data_policy_changing = False
                if dpchange["message"]:
                    self._on_data_policy_change(dpchange["message"], elogbook)

    def _on_data_policy_change(self, message: str, elogbook: bool = True) -> None:
        self._emit_data_policy_event(message)

        if elogbook:
            message += f"\nData path: {self.root_path}"
            logtools.elog_info(message)

        confirmed, unconfirmed = self.icat_confirm_datasets()
        nunconfirmed = len(unconfirmed)
        if nunconfirmed > 1:
            message += (
                f"\nNumber of unconfirmed ICAT dataset registrations: {nunconfirmed}"
            )
            if confirmed is None:
                message += f" ({self.icat_client.reason_for_missing_information})"
        print(message)

    def _emit_data_policy_event(self, message: str) -> None:
        if self._testing:
            print("Emit session event:", message)
            return
        session_name = self.session
        session = current_session
        try:
            is_current_session = session.name == session_name
        except AttributeError:
            is_current_session = False
        if not is_current_session:
            session = get_config().get(self._session_name)
        session._emit_event(
            ESRFDataPolicyEvent.Change, message=message, data_path=self.root_path
        )

    #############
    ### ICAT ####
    #############

    @property
    def proposal(self) -> Optional[Proposal]:
        """The proposal will be created in Redis when it does not exist yet."""
        if self._proposal_object is None:
            self._proposal_object = self._get_proposal_object(create=True)
        return self._proposal_object

    @property
    def collection(self) -> Optional[DatasetCollection]:
        """The collection will be created in Redis when it does not exist yet."""
        if self._collection_object is None:
            self._collection_object = self._get_collection_object(create=True)
        return self._collection_object

    @property
    def dataset(self) -> Optional[Dataset]:
        """The dataset will be created in Redis when it does not exist yet."""
        if self._dataset_object is None:
            self._dataset_object = self._get_dataset_object(create=True)
        return self._dataset_object

    @property
    def sample(self) -> Optional[DatasetCollection]:
        return self.collection

    @property
    def sample_name(self) -> Optional[str]:
        """Sample name is an ICAT metadata field. Collection name is a `base_path` field. They can be identical."""
        if self.dataset is not None:
            return self.dataset.sample_name

    @property
    def icat_base_path(self) -> str:
        """ICAT root directory depending in the proposal type (inhouse, visitor, tmp)"""
        return self.eval_template(self._get_base_path_template(icat=True))

    @property
    def icat_root_path(self) -> str:
        """Directory of the scan *data file* reachable by ICAT."""
        return self.root_path.replace(self.base_path, self.icat_base_path)

    @property
    def icat_data_path(self) -> str:
        """Full path for the scan *data file* without the extension reachable by ICAT."""
        return self.data_path.replace(self.base_path, self.icat_base_path)

    @property
    def icat_data_fullpath(self) -> str:
        """Full path for the scan *data file* with the extension reachable by ICAT."""
        return self.data_fullpath.replace(self.base_path, self.icat_base_path)

    @property
    def icat_client(self) -> IcatClientInterface:
        if self._icat_client is None:
            config = self.icat_client_config
            self._icat_client = icat_client_from_config(config)
        return self._icat_client

    @property
    def icat_client_config(self) -> dict:
        if self.proposal_type == "tmp" or self._testing:
            return {"disable": True}
        return icat_client_config(
            bliss_session=self.session,
            proposal=self.proposal_name,
            beamline=self.beamline,
        )

    def _close_proposal(self) -> None:
        """Close the current proposal:

        - check whether datasets are registered with ICAT
        - close the current collection
        - clean up the proposal tree from Redis.
        """
        if self._proposal:
            self._save_unconfirmed_datasets(raise_on_error=False)

        self._close_collection()

        proposal = self._get_proposal_object()
        if proposal is not None:
            # clear proposal from the json policy tree in Redis
            node = proposal._node
            node._tree.delete_node(node._path)

        self._proposal_object = None
        self._icat_client = None

    def _close_collection(self):
        """Close the current collection:

        - close the current dataset
        """
        self._close_dataset()
        self._collection_object = None

    def _close_dataset(self) -> None:
        """Close the current dataset. This will NOT create the dataset in Redis
        if it does not exist yet. If the dataset if already closed it does NOT
        raise an exception.
        """
        dataset = self._dataset_object

        if dataset is None:
            # The dataset object has not been cached
            dataset = self._get_dataset_object(create=False)
            if dataset is None:
                # The Redis node has not been created
                return

        if dataset.is_closed:
            # Already closed: nothing to do
            self._dataset_object = None
            return

        # Mark as "closed" in Redis and send to ICAT
        dataset.close(self.icat_client)
        self._dataset_object = None

    def _init_dataset_metadata(self) -> None:
        """The dataset will be created in Redis if it does not exist already.
        Metadata will be gathered if not already done. RuntimeError is raised
        when the dataset is already closed.
        """
        dataset = self.dataset  # Created in Redis when missing
        if dataset is None:
            return
        if dataset.is_closed:
            raise RuntimeError("Dataset is already closed (choose a different name)")
        dataset.gather_metadata(on_exists="skip")

    def _get_proposal_object(self, create: bool = True) -> Optional[Proposal]:
        node = self._get_redis_node("proposal", create=create)
        if node is not None:
            return Proposal(node)

    def _get_collection_object(
        self, create: bool = True
    ) -> Optional[DatasetCollection]:
        node = self._get_redis_node("dataset_collection", create=create)
        if node is not None:
            return DatasetCollection(node)

    def _get_dataset_object(self, create: bool = True) -> Optional[Dataset]:
        node = self._get_redis_node("dataset", create=create)
        if node is not None:
            return Dataset(node)

    def _get_redis_node(
        self, node_type: str, create: bool = True
    ) -> Optional[RedisJsonNode]:
        """Return the Redis node accociated with :code:`db_path_items`.
        When the node does not exist, create it when :code:`create=True`
        or return :code:`None` when :code:`create=False`.
        """
        if self._testing:
            return
        groups = list()
        if self._proposal:
            groups.append(("proposal", self.proposal_name))
            if self._collection:
                groups.append(("dataset_collection", self.collection_name))
                if self._dataset:
                    groups.append(("dataset", self.dataset_name))
        if not groups:
            return

        data_policy_tree = RedisJsonTree(f"data_policy:{self.session}")

        node_sel = None
        node_path = ""
        for item_type, item_name in groups:
            node_path += "/" + item_name
            with _DATAPOLICY_NODE_LOCK:
                try:
                    node = data_policy_tree.get_node(node_path)
                except KeyError:
                    if create:
                        node = data_policy_tree.create_node(node_path)
                        self._init_redis_node_info(node, item_type)
                    else:
                        break
            if item_type == node_type:
                node_sel = node
                break

        return node_sel

    def _init_redis_node_info(self, node: RedisJsonNode, node_type: str) -> None:
        """Add missing keys to node info"""
        if node_type == "proposal":
            info = {
                "__name__": self.proposal_name,
                "__path__": self._icat_proposal_path,
                "__metadata__": {},
                "__frozen__": False,
            }
        elif node_type == "dataset_collection":
            info = {
                "__name__": self.collection_name,
                "__path__": self._icat_collection_path,
                "__metadata__": {"Sample_name": self.collection_name},
                "__frozen__": False,
            }
        elif node_type == "dataset":
            info = {
                "__name__": self.dataset_name,
                "__path__": self._icat_dataset_path,
                "__metadata__": {
                    "startDate": datetime.datetime.now().astimezone().isoformat()
                },
                "__frozen__": False,
                "__closed__": False,
                "__registered__": False,
            }
        else:
            return

        node_info = node.get()
        update = False
        for k, v in info.items():
            if k not in node_info:
                node_info[k] = v
                update = True
        if update:
            node.set(node_info)

    @property
    def _icat_proposal_path(self) -> str:
        # See template
        return os.sep.join(self.icat_root_path.split(os.sep)[:-3])

    @property
    def _icat_collection_path(self) -> str:
        # See template
        return os.sep.join(self.icat_root_path.split(os.sep)[:-1])

    @property
    def _icat_dataset_path(self) -> str:
        # See template
        return self.icat_root_path

    def icat_register_datasets(
        self, raise_on_error=True, timeout: Optional[float] = None
    ):
        _, unconfirmed = self.icat_confirm_datasets(timeout=timeout)
        if not unconfirmed:
            print("All datasets are already registered in ICAT")
        for dataset_id in unconfirmed:
            self.icat_register_dataset(dataset_id, raise_on_error=raise_on_error)
        print("")
        self.icat_info(timeout=timeout)

    def _save_unconfirmed_datasets(
        self, raise_on_error: bool = True, timeout: Optional[float] = 10
    ) -> bool:
        print(
            f"Check whether all datasets are registered with ICAT ... (timeout = {timeout} s)"
        )
        t0 = time.time()
        unconfirmed = set()
        while (time.time() - t0) < timeout:
            _, unconfirmed = self.icat_confirm_datasets(timeout=timeout)
            if not unconfirmed:
                break
            gevent.sleep(0.5)

        if not unconfirmed:
            return
        msg = f'Unconfirmed datasets are stored in {self.icat_directory}\nYou may need to send them to ICAT manually with the command\n\n  icat-store-from-file "{self.icat_directory}/*.xml"\n'
        logtools.log_warning(self.proposal or self, msg)
        logtools.elog_warning(msg)
        for dataset_id in unconfirmed:
            self.icat_save_dataset(dataset_id, raise_on_error=raise_on_error)

    def icat_investigation_info(self, timeout: Optional[float] = None) -> None:
        print(
            self.icat_client.investigation_info_string(
                beamline=self.beamline, proposal=self.proposal_name, timeout=timeout
            )
        )

    def icat_dataset_info(self, timeout: Optional[bool] = None) -> None:
        confirmed, unconfirmed = self.icat_confirm_datasets(timeout=timeout)
        if confirmed is None:
            print(f"Datasets: {len(unconfirmed)} unconfirmed, ??? confirmed")
        else:
            print(
                f"Datasets: {len(unconfirmed)} unconfirmed, {len(confirmed)} confirmed"
            )
        print("")
        if self.proposal is not None:
            dataset_info = self.proposal.unconfirmed_dataset_info_string()
            if dataset_info:
                print(dataset_info)

    def icat_info(self, timeout: Optional[float] = None) -> None:
        self.icat_investigation_info(timeout=timeout)
        print("")
        self.icat_dataset_info(timeout=timeout)

    def icat_confirm_datasets(
        self, timeout: Optional[float] = None
    ) -> tuple[list, list]:
        """Compare the list of unconfirmed datasets in Redis with
        the list of confirmed dataset in ICAT and confirm datasets.
        """
        proposal = self.proposal
        if proposal is None:
            return list(), list()
        proposal_name = self.proposal_name
        beamline = self.beamline
        confirmed = self.icat_client.registered_dataset_ids(
            beamline=beamline, proposal=proposal_name, timeout=timeout
        )
        unconfirmed = proposal.unconfirmed_dataset_ids
        if confirmed is not None:
            for dataset_id in set(unconfirmed) & set(confirmed):
                dataset = proposal.get_dataset(dataset_id)
                if dataset is None:
                    continue
                dataset.confirm_registration()
        return confirmed, proposal.unconfirmed_dataset_ids

    def icat_register_dataset(
        self, dataset_name_or_id: DatasetId | str, raise_on_error: bool = True
    ):
        """Send dataset metadata to ICAT."""
        try:
            dset = self._get_dataset(dataset_name_or_id)
            dset.register_with_icat(self.icat_client, raise_on_error=raise_on_error)
        except Exception as e:
            if raise_on_error:
                raise
            logtools.log_exception(self.proposal or self, str(e))
        else:
            print(f"Dataset '{dataset_name_or_id}' has been send to ICAT")

    def icat_save_dataset(
        self, dataset_name_or_id: DatasetId | str, raise_on_error: bool = True
    ):
        """Save dataset metadata on disk."""
        try:
            dset = self._get_dataset(dataset_name_or_id)

            basename = os.path.splitext(os.path.basename(dset.path))[0] + ".xml"
            dirname = os.path.join(self.icat_directory, basename)

            dset.save_for_icat(self.icat_client, dirname)
        except Exception as e:
            if raise_on_error:
                raise
            logtools.elog_error(str(e))
            logtools.log_exception(self.proposal or self, str(e))
        else:
            print(f"Dataset '{dataset_name_or_id}' has been saved")

    @property
    def icat_directory(self) -> str:
        dirname = os.path.dirname(os.path.dirname(os.path.dirname(self.filename)))
        return os.path.join(dirname, "__icat__")

    def _get_dataset(self, dataset_name_or_id: DatasetId | str) -> Dataset:
        proposal = self.proposal
        dataset = proposal.get_dataset(dataset_name_or_id)
        if dataset is None:
            raise RuntimeError(
                f"dataset '{dataset_name_or_id}' does not exist in Redis"
            )
        if is_null_client(self.icat_client):
            raise RuntimeError(
                f"Dataset '{dataset_name_or_id}' cannot be send to ICAT ({self.icat_client.reason_for_missing_information})"
            )
        return dataset


_d = r"([0-2]\d|3[01])"
_m = r"(0\d|1[0-2])"
_Y = r"(\d{4})"
_PROPOSAL_SESSION_REGEX = re.compile(f"^{_Y}{_m}{_d}$")

# Characters that cannot be used in file or directory names:
#  - os.sep is forbidding because it adds another directory level
#  - "{" and "}" are forbidden because the name is used in
#    “new style” string formatting (see `ESRFScanSaving.template`)
#  - "%" is forbidden because the name can be used in “old style”
#    string formatting (% Operator)
#  - the null byte "\x00" is forbidden because in C it marks the end
#    of a string
#  - paths need to be accesible from Windows
#    https://learn.microsoft.com/en-us/windows/win32/fileio/naming-a-file
#  - paths might be used within an url. So it must not conflict with it synthax
_FORBIDDEN_CHARS_BASE = {os.sep, "{", "}", "%", "\x00"}
_FORBIDDEN_CHARS_WINDOWS = {"<", ">", ":", '"', "'", "/", "\\", "|", "?", "*"} | {
    chr(i) for i in range(32)
}
_FORBIDDEN_CHARS_URL = {"@", "#", ":", "?"}
_FORBIDDEN_CHARS = (
    _FORBIDDEN_CHARS_BASE | _FORBIDDEN_CHARS_WINDOWS | _FORBIDDEN_CHARS_URL
)


def _check_valid_in_path(value: str) -> None:
    """Checks whether the string can be use in a file or directory name"""
    forbidden = set(value) & set(_FORBIDDEN_CHARS)
    if forbidden:
        forbidden = ", ".join([repr(c) for c in forbidden])
        raise ValueError(f"Forbidden characters were used: {forbidden}")
