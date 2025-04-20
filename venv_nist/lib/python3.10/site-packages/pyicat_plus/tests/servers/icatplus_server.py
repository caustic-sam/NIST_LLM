import os
import re
import socket
import json
import http.server
import logging
from typing import Iterator, Tuple, Optional

from .icat_db import IcatDb
from .icat_session import IcatSession
from .utils import ReuseAddrTCPServer
from ...utils.log_utils import basic_config

logger = logging.getLogger("ICATPLUS SERVER")


class MyTCPRequestHandler(http.server.BaseHTTPRequestHandler):
    def __init__(self, *args, s_out=None, icat_data_dir: Optional[str] = None, **kw):
        self.s_out = s_out
        self.icatdb = IcatDb(icat_data_dir)
        self.icatsession = IcatSession(icat_data_dir)
        super().__init__(*args, **kw)

    def do_HEAD(self):
        logger.info("HEAD")
        self.reply_ok()

    def do_GET(self):
        if self._handle_investigation():
            return
        if self._handle_dataset():
            return
        if self._handle_get_investigations_by():
            return
        if self._handle_get_parcels_by():
            return
        logger.error("Bad GET request: %s", self.path)
        self.reply_bad_request()

    def do_POST(self):
        if self._handle_elogbook_message():
            return
        if self._handle_elogbook_data():
            return
        if self._handle_elogbook_message_beamline_only():
            return
        if self._handle_elogbook_message_no_beamline():
            return
        if self._handle_elogbook_data_beamline_only():
            return
        if self._handle_login():
            return
        logger.error("Bad POST request: %s", self.path)
        self.reply_bad_request()

    def _handle_investigation(self):
        fmt = "/dataacquisition/(?P<apikey>[^//]+)/investigation\\?instrumentName=(?P<instrument>[^//]+)&investigationName=(?P<investigation>[^//]+)&?(?P<parameters>[^//]+)?"
        mresult = re.match(fmt, self.path)
        if not mresult:
            return
        data = mresult.groupdict()
        if data["parameters"]:
            data["parameters"] = dict(
                s.split("=") for s in data["parameters"].split("&")
            )

        if "666" in data["investigation"]:
            self.reply_error()
            return True

        investigations = self.icatdb.get_investigations(
            data["instrument"], data["investigation"]
        )
        # TODO: use parameters to filter
        logger.info("Investigation GET response: %s", investigations)

        self.reply_json(investigations)
        return True

    def _iter_investigations(self) -> Iterator[Tuple[int, dict]]:
        root_dir = os.path.join(self.icat_data_dir, "investigations")
        if not os.path.isdir(root_dir):
            return
        for basename in os.listdir(root_dir):
            filename = os.path.join(root_dir, basename)
            investigation_id = int(os.path.splitext(basename)[0])
            with open(filename, "r") as f:
                investigation = json.load(f)
            yield investigation_id, investigation

    def _handle_dataset(self):
        fmt = "/dataacquisition/(?P<apikey>[^//]+)/dataset\\?investigationId=(?P<investigation_id>[^//]+)&?(?P<parameters>[^//]+)?"
        mresult = re.match(fmt, self.path)
        if not mresult:
            return
        data = mresult.groupdict()
        if data["parameters"]:
            data["parameters"] = dict(
                s.split("=") for s in data["parameters"].split("&")
            )

        datasets = self.icatdb.get_datasets(mresult["investigation_id"])
        # TODO: use parameters to filter
        logger.info("Dataset GET response: %s", datasets)
        self.reply_json(datasets)
        return True

    def _handle_elogbook_message(self):
        logger.info(self.path)
        fmt = "/dataacquisition/(?P<apikey>[^//]+)/notification\\?instrumentName=(?P<instrument>[^//]+)&investigationName=(?P<investigation>[^//]+)"
        mresult = re.match(fmt, self.path)
        if not mresult:
            return
        if (
            self.headers.get("content-type") != "application/json"
            or "666" in mresult.groupdict()["investigation"]
        ):
            self.reply_bad_request()
            return True
        length = int(self.headers.get("content-length"))
        adict = json.loads(self.rfile.read(length))
        adict.update(mresult.groupdict())
        self.on_elog_message(adict)
        self.reply_ok()
        return True

    def _handle_elogbook_data(self):
        fmt = "/dataacquisition/(?P<apikey>[^//]+)/base64\\?instrumentName=(?P<instrument>[^//]+)&investigationName=(?P<investigation>[^//]+)"
        mresult = re.match(fmt, self.path)
        if not mresult:
            return
        if (
            self.headers.get("content-type") != "application/json"
            or "666" in mresult.groupdict()["investigation"]
        ):
            self.reply_bad_request()
            return True
        length = int(self.headers.get("content-length"))
        adict = json.loads(self.rfile.read(length))
        adict.update(mresult.groupdict())
        self.on_elog_message(adict)
        self.reply_ok()
        return True

    def _handle_elogbook_message_beamline_only(self):
        logger.info(self.path)
        fmt = "/dataacquisition/(?P<apikey>[^//]+)/notification\\?instrumentName=(?P<instrument>[^//]+)"
        mresult = re.match(fmt, self.path)
        if not mresult:
            return
        if self.headers.get("content-type") != "application/json":
            self.reply_bad_request()
            return True
        length = int(self.headers.get("content-length"))
        adict = json.loads(self.rfile.read(length))
        adict.update(mresult.groupdict())
        self.on_elog_message(adict)
        self.reply_ok()
        return True

    def _handle_elogbook_message_no_beamline(self):
        logger.info(self.path)
        fmt = "/dataacquisition/(?P<apikey>[^//]+)/notification"
        mresult = re.match(fmt, self.path)
        if not mresult:
            return
        if self.headers.get("content-type") != "application/json":
            self.reply_bad_request()
            return True
        length = int(self.headers.get("content-length"))
        adict = json.loads(self.rfile.read(length))
        adict.update(mresult.groupdict())
        self.on_elog_message(adict)
        self.reply_ok()
        return True

    def _handle_elogbook_data_beamline_only(self):
        fmt = "/dataacquisition/(?P<apikey>[^//]+)/base64\\?instrumentName=(?P<instrument>[^//]+)"
        mresult = re.match(fmt, self.path)
        if not mresult:
            return
        if self.headers.get("content-type") != "application/json":
            self.reply_bad_request()
            return True
        length = int(self.headers.get("content-length"))
        adict = json.loads(self.rfile.read(length))
        adict.update(mresult.groupdict())
        self.on_elog_message(adict)
        self.reply_ok()
        return True

    def _handle_login(self):
        fmt = "/session"
        mresult = re.match(fmt, self.path)
        if not mresult:
            return
        if self.headers.get("content-type") != "application/json":
            self.reply_bad_request()
            return True
        length = int(self.headers.get("content-length"))
        credentials = json.loads(self.rfile.read(length))

        try:
            icat_session = self.icatsession.login(credentials)
        except PermissionError:
            self.reply_forbidden(message="Authentication failed")
        else:
            self.reply_json(icat_session)

        return True

    def _handle_get_investigations_by(self):
        fmt = r"catalogue/([a-f0-9-]{36})/investigation"
        mresult = re.search(fmt, self.path)
        if not mresult:
            return
        session_id = mresult.group(1)
        if self.icatsession.is_allowed(session_id):
            self.reply_json(self.icatdb.get_all_investigations())
        else:
            self.reply_forbidden(message="Authentication failed")
        return True

    def _handle_get_parcels_by(self):
        fmt = r"tracking/([a-f0-9-]{36})/parcel\?investigationId=(\d+)"
        mresult = re.search(fmt, self.path)
        if not mresult:
            return
        session_id = mresult.group(1)
        if self.icatsession.is_allowed(session_id):
            self.reply_json(list())
        else:
            self.reply_forbidden(message="Authentication failed")
        return True

    def reply_ok(self):
        self.send_response(http.HTTPStatus.OK)
        self.end_headers()

    def reply_bad_request(self):
        self.send_response(http.HTTPStatus.BAD_REQUEST)
        self.end_headers()

    def reply_not_found(self):
        self.send_response(http.HTTPStatus.NOT_FOUND)
        self.end_headers()

    def reply_error(self):
        self.send_response(http.HTTPStatus.INTERNAL_SERVER_ERROR)
        self.end_headers()

    def reply_forbidden(self, **kw):
        self.send_response(http.HTTPStatus.FORBIDDEN, **kw)
        self.end_headers()

    def reply_json(self, adict):
        body = json.dumps(adict).encode()
        self.send_response(http.HTTPStatus.OK)
        self.send_header("Content-type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def on_elog_message(self, adict):
        if not adict:
            return
        logger.info("received message:\n %s", adict)
        if self.s_out is not None:
            self.s_out.sendall(json.dumps(adict).encode() + b"\n")


def main(port=8443, port_out=0, icat_data_dir: Optional[str] = None):
    if port_out:
        s_out = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s_out.connect(("localhost", port_out))
    else:
        s_out = None

    class MyTCPRequestHandlerWithSocket(MyTCPRequestHandler):
        def __init__(self, *args, **kw):
            super().__init__(*args, s_out=s_out, icat_data_dir=icat_data_dir, **kw)

    # Create a TCP Server instance
    aServer = ReuseAddrTCPServer(("localhost", port), MyTCPRequestHandlerWithSocket)

    # Start accepting requests and setup output socket
    if port_out:
        logger.info(f"Redirect received messages to port {port_out}")
        s_out.sendall(json.dumps({"STATUS": "LISTENING"}).encode() + b"\n")
    try:
        logger.info("CTRL-C to stop")
        aServer.serve_forever()
    finally:
        if port_out:
            s_out.close()
        logger.info("Exit.")


if __name__ == "__main__":
    import argparse

    basic_config(
        logger=logger,
        level=logging.DEBUG,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    parser = argparse.ArgumentParser(description="ICAT plus server")
    parser.add_argument("--port", default=8443, type=int, help="server port")
    parser.add_argument("--port_out", default=0, type=int, help="output socket")
    parser.add_argument(
        "--icat_data_dir", default=None, type=str, help="One file per dataset"
    )
    args = parser.parse_args()
    main(port=args.port, port_out=args.port_out, icat_data_dir=args.icat_data_dir)
