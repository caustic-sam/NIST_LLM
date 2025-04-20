from __future__ import annotations
import logging
from typing import Union, Optional

from pyicat_plus.client.main import IcatClient as _IcatClient
from pyicat_plus.client.null import IcatNullClient
from pyicat_plus.client.interface import DatasetId  # noqa: F401
from pyicat_plus.client.interface import IcatClientInterface  # noqa: F401

from bliss.config.static import get_config
from bliss import current_session
from bliss import __version__

logger = logging.getLogger(__name__)


def icat_client_is_disabled() -> bool:
    config = get_config().root.get("icat_servers")
    if config:
        return config.get("disable", False)
    else:
        return True


def icat_client_config(
    bliss_session: str, beamline: str, proposal: Optional[str] = None
) -> dict:
    config = get_config().root.get("icat_servers")
    if config:
        config = dict(config)
        if proposal is not None:
            config["proposal"] = proposal
        config["beamline"] = beamline
        disable = config.pop("disable", False)
        return {"disable": disable, "kwargs": config}
    else:
        return {"disable": True}


def icat_client_from_config(config: dict) -> IcatNullClient | IcatClient:
    if config["disable"]:
        return IcatNullClient(expire_datasets_on_close=False)
    else:
        return IcatClient(**config.get("kwargs", dict()))


def is_null_client(icat_client: Union["IcatClient", IcatNullClient]):
    return isinstance(icat_client, IcatNullClient)


class IcatClient(_IcatClient):
    """The value of all properties is retrieved from the Bliss session.
    This means the value is `None` when no session exists (e.g. using Bliss
    as a library). Exceptions are the proposal and the beamline which
    will fall back to the value set by the corresponding setters.
    """

    def __init__(self, *args, **kw) -> None:
        elogbook_metadata = kw.setdefault("elogbook_metadata", dict())
        elogbook_metadata.setdefault("software", "Bliss_v" + __version__)
        self.__current_proposal: str | None = None
        self.__current_beamline: str | None = None
        super().__init__(*args, **kw)

    @property
    def current_proposal(self):
        if self.__current_proposal is None or current_session:
            self.__current_proposal = current_session.scan_saving.proposal_name
        return self.__current_proposal

    @current_proposal.setter
    def current_proposal(self, value: Optional[str]):
        self.__current_proposal = value

    @property
    def current_beamline(self):
        if self.__current_beamline is None or current_session:
            self.__current_beamline = current_session.scan_saving.beamline
        return self.__current_beamline

    @current_beamline.setter
    def current_beamline(self, value: Optional[str]):
        self.__current_beamline = value

    @property
    def current_dataset(self):
        return current_session.scan_saving.dataset_name

    @current_dataset.setter
    def current_dataset(self, value: Optional[str]):
        pass

    @property
    def current_dataset_metadata(self):
        return current_session.scan_saving.dataset.get_current_icat_metadata()

    @current_dataset_metadata.setter
    def current_dataset_metadata(self, value: Optional[str]):
        pass

    @property
    def current_collection(self):
        return current_session.scan_saving.collection

    @current_collection.setter
    def current_collection(self, value: Optional[str]):
        pass

    @property
    def current_path(self):
        return current_session.scan_saving.icat_root_path

    @current_path.setter
    def current_path(self, value: Optional[str]):
        pass
