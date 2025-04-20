import base64
import socket
import logging
import datetime
import mimetypes
import warnings
from enum import Enum
from urllib.parse import urljoin
from typing import Optional, Iterable, List, Tuple

import requests

from ..utils.url import normalize_url
from . import defaults
from importlib.metadata import version as get_version

release = get_version("pyicat_plus")

logger = logging.getLogger(__name__)


class IcatElogbookClient:
    """Client for the e-logbook part of the ICAT+ REST API.

    REST API docs:
    https://icatplus.esrf.fr/api-docs/

    The ICAT+ server project:
    https://gitlab.esrf.fr/icat/icat-plus/-/blob/master/README.md
    """

    DEFAULT_SCHEME = "https"

    def __init__(
        self,
        url: str,
        api_key: Optional[str] = None,
        timeout: Optional[float] = None,
        **payload,
    ):
        if api_key is None:
            api_key = defaults.ELOGBOOK_TOKEN
        url = normalize_url(url, default_scheme=self.DEFAULT_SCHEME)

        path = f"dataacquisition/{api_key}/notification"
        self._message_url = urljoin(url, path)

        path = f"dataacquisition/{api_key}/base64"
        self._data_url = urljoin(url, path)

        self._init_payload = payload
        self._init_payload.setdefault("machine", socket.getfqdn())
        self._init_payload.setdefault("software", "pyicat-plus_v" + release)

        self.raise_error = True
        if timeout is None:
            timeout = 0.1
        self.timeout = timeout

    def _merge_payloads(self, message_payload: dict, call_payload: dict) -> dict:
        payloads = self._sorted_payloads(message_payload, call_payload)
        result = {k: v for payload in payloads for k, v in payload.items()}
        tags = self._merge_payload_tags(*payloads)
        if tags:
            result.pop("tags", None)
            result["tag"] = tags
        return result

    def _sorted_payloads(self, message_payload: dict, call_payload: dict) -> List[dict]:
        """Sorted by increasing priority"""
        return [message_payload, self._init_payload, call_payload]

    def _merge_payload_tags(self, *payloads: Iterable[dict]) -> List[dict]:
        """The payload tags can be eithers a list of strings or a list of dictionaries.
        The return value are the merged tags as a list of dictionaries.
        """
        names = set()
        tags = list()
        for payload in payloads:
            ptags = payload.get("tag", list()) + payload.get("tags", list())
            for tag in ptags:
                if isinstance(tag, str):
                    if tag in names:
                        continue
                    names.add(tag)
                    tags.append({"name": tag})
                else:
                    if tag["name"] in names:
                        continue
                    names.add(tag["name"])
                    tags.append(tag)
        return tags

    def _post_with_payload(
        self, url: str, message_payload: dict, call_payload: dict
    ) -> None:
        payload = self._merge_payloads(message_payload, call_payload)
        payload.setdefault(
            "creationDate", datetime.datetime.now().astimezone().isoformat()
        )
        try:
            response = requests.post(url, json=payload, timeout=self.timeout)
        except requests.exceptions.ReadTimeout:
            return  # we have no confirmation that the call succeeded
        except Exception as e:
            if self.raise_error:
                raise
            logger.exception(e)
            return
        if self.raise_error:
            response.raise_for_status()
        elif not response.ok:
            logger.error("%s: %s", response, response.text)

    def send_message(
        self,
        message: str,
        message_type: Optional[str] = None,
        editable: Optional[bool] = None,
        formatted: Optional[bool] = None,
        mimetype: Optional[str] = None,
        beamline: Optional[str] = None,
        proposal: Optional[str] = None,
        dataset: Optional[str] = None,
        **call_payload,
    ):
        url = self._compose_url(
            url=self._message_url, beamline=beamline, proposal=proposal
        )
        message_payload = self._encode_message(
            message,
            message_type=message_type,
            editable=editable,
            formatted=formatted,
            mimetype=mimetype,
            dataset=dataset,
        )
        self._post_with_payload(url, message_payload, call_payload)

    def send_binary_data(
        self,
        data: bytes,
        mimetype: str,
        beamline: Optional[str] = None,
        proposal: Optional[str] = None,
        **call_payload,
    ):
        url = self._compose_url(
            url=self._data_url, beamline=beamline, proposal=proposal
        )
        message_payload = self._encode_binary_data(data, mimetype=mimetype)
        self._post_with_payload(url, message_payload, call_payload)

    @staticmethod
    def _compose_url(
        url: str, beamline: Optional[str] = None, proposal: Optional[str] = None
    ):
        query = {}
        if beamline:
            query["instrumentName"] = beamline
        if proposal:
            query["investigationName"] = proposal
        query = "&".join([f"{k}={v}" for k, v in query.items()])
        return f"{url}?{query}"

    def send_text_file(
        self,
        filename: str,
        beamline: Optional[str] = None,
        proposal: Optional[str] = None,
        dataset: Optional[str] = None,
        message_type: Optional[str] = None,
        editable: Optional[bool] = None,
        formatted: Optional[bool] = None,
        mimetype: Optional[str] = None,
        **payload,
    ):
        with open(filename, "r") as f:
            message = f.read()
        self.send_message(
            message,
            message_type=message_type,
            proposal=proposal,
            beamline=beamline,
            dataset=dataset,
            editable=editable,
            formatted=formatted,
            mimetype=mimetype,
            **payload,
        )

    def send_binary_file(
        self,
        filename: str,
        beamline: Optional[str] = None,
        proposal: Optional[str] = None,
        **payload,
    ):
        with open(filename, "rb") as f:
            data = f.read()
        mimetype, _ = mimetypes.guess_type(filename, strict=True)
        self.send_binary_data(
            data, mimetype=mimetype, beamline=beamline, proposal=proposal, **payload
        )

    def _encode_message(
        self,
        message: str,
        message_type: Optional[str] = None,
        editable: Optional[bool] = None,
        formatted: Optional[bool] = None,
        mimetype: Optional[str] = None,
        dataset: Optional[str] = None,
    ) -> dict:
        message_category, message_type = _message_category_and_type(
            message_type=message_type, editable=editable, formatted=formatted
        )
        if mimetype is None:
            mimetype = "text/plain"
        try:
            format = _MessageFormatMapping[mimetype]
        except KeyError:
            raise ValueError(
                f"mime type '{mimetype}' is not supported ({list(_MessageFormatMapping)})"
            ) from None
        message = {
            "type": message_type.name,
            "category": message_category.name,
            "content": [{"format": format.name, "text": message}],
        }
        if dataset:
            message["datasetName"] = dataset
        return message

    def _encode_binary_data(
        self,
        data: bytes,
        mimetype: Optional[str] = None,
    ) -> dict:
        if not mimetype:
            # arbitrary binary data
            mimetype = "application/octet-stream"
        data_header = f"data:{mimetype};base64,"
        data_blob = base64.b64encode(data).decode("latin-1")
        return {"base64": data_header + data_blob}


_MessageCategory = Enum("MessageCategory", "debug info error commandLine comment")
_MessageType = Enum("MessageType", "annotation notification")
_MessageFormat = Enum("MessageType", "plainText html")

_MessageCategoryMapping = {
    "debug": _MessageCategory.debug,
    "info": _MessageCategory.info,
    "warning": _MessageCategory.error,
    "warn": _MessageCategory.error,
    "error": _MessageCategory.error,
    "critical": _MessageCategory.error,
    "fatal": _MessageCategory.error,
    "command": _MessageCategory.commandLine,
    "comment": _MessageCategory.comment,
}

_MessageFormatMapping = {
    "text/plain": _MessageFormat.plainText,
    "text/html": _MessageFormat.html,
}


def _message_category_and_type(
    message_type: Optional[str] = None,
    editable: Optional[bool] = None,
    formatted: Optional[bool] = None,
) -> Tuple[_MessageCategory, _MessageType]:
    """Derive the ICAT message category from the API message type.

    The ICAT message types are:

    - "annotation" (default for comments): editable and unformatted message
    - "notification" (default for non-comments): formatted and not editable

    Only comments can be editable and a message cannot be editable and
    formatted at the same time.
    """
    if message_type is None:
        message_type = "comment"
    try:
        category = _MessageCategoryMapping[message_type.lower()]
    except KeyError:
        raise ValueError(
            f"'{message_type}' is not a valid e-logbook message type"
        ) from None

    if category != _MessageCategory.comment:
        # Non-comments cannot be editable
        if editable:
            warnings.warn(
                f"message type '{message_type}' cannot be editable", UserWarning
            )
            editable = None

        # Non-comments cannot be unformatted
        if formatted is not None and not formatted:
            warnings.warn(
                f"message type '{message_type}' cannot be unformatted", UserWarning
            )
            formatted = None

    # A message cannot be editable and formatted at the same time or uneditable and unformatted
    if formatted == editable and formatted is not None:
        if formatted:
            warnings.warn(
                f"message type '{message_type}' cannot be editable and formatted at the same time",
                UserWarning,
            )
        else:
            warnings.warn(
                f"message type '{message_type}' cannot be uneditable and unformatted at the same time",
                UserWarning,
            )
        formatted = None
        editable = None

    # Editability is specified
    if editable is not None:
        if editable:
            return category, _MessageType.annotation
        return category, _MessageType.notification

    # Formatability is specified
    if formatted is not None:
        if formatted:
            return category, _MessageType.notification
        return category, _MessageType.annotation

    # By default comments are annotations and the rest are notifications
    if category == _MessageCategory.comment:
        return category, _MessageType.annotation
    return category, _MessageType.notification
