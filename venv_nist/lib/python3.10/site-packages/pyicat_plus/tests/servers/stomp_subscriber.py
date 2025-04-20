import os
import json
import socket
import logging
import threading
from enum import Enum
from typing import Optional
from json.decoder import JSONDecodeError

import stomp
import xmltodict
from xml.parsers.expat import ExpatError

from .icat_db import IcatDb
from ...utils.log_utils import basic_config
from .activemq_rest_server import ICAT_QUEUES

logger = logging.getLogger("STOMP SUBSCRIBER")

MessageType = Enum("MessageType", "investigation dataset archiving addfiles unknown")


class MyListener(stomp.ConnectionListener):
    def __init__(self, conn, icat_data_dir: Optional[str] = None):
        self.conn = conn
        self.s_out = None
        self.icatdb = IcatDb(icat_data_dir)
        super().__init__()

    def redirect_messages(self, port):
        if self.s_out is not None:
            self.s_out.close()
        self.s_out = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.s_out.connect(("localhost", port))
        logger.info(f"Redirect received messages to port {port}")

    def on_message(self, frame):
        message = frame.body
        logger.info("received message:\n %s", message)

        message_type = None
        try:
            message = xmltodict.parse(
                message,
                process_namespaces=True,
                namespaces={"http://www.esrf.fr/icat": None},
            )
        except ExpatError:
            try:
                message = json.loads(message)
            except JSONDecodeError:
                message_type = MessageType.unknown
            else:
                keys = set(message)
                if keys == {"datasetId", "type", "level", "message"}:
                    message_type = MessageType.archiving
                elif keys == {"datasetId"}:
                    message_type = MessageType.addfiles
                else:
                    message_type = MessageType.unknown
        else:
            if "investigation" in message:
                message_type = MessageType.investigation
            elif "dataset" in message:
                message_type = MessageType.dataset

        logger.info("parsed message (%s):\n %s", message_type, message)

        # Only access specific destinations
        header = frame.headers
        icat_queues = ["/queue/" + q for q in ICAT_QUEUES]
        if header.get("destination") not in icat_queues:
            return

        # Only accept valid proposals
        if message_type in (message_type.investigation, message_type.dataset):
            if message_type is message_type.investigation:
                data = message["investigation"]
                proposal = data["experiment"]
            else:
                data = message["dataset"]
                proposal = data["investigation"]
                # Convert to backend format
                file_count = 0
                if os.path.exists(data["location"]):
                    file_count = len(os.listdir(data["location"]))
                data["parameter"].append({"name": "__fileCount", "value": file_count})
                data["parameters"] = data.pop("parameter")
            if proposal and "666" in proposal:
                logger.info(
                    "Do not register %s for invalid proposal '%s'",
                    message_type,
                    proposal,
                )
                return

        # Store data
        if message_type in (message_type.investigation, message_type.dataset):
            if message_type is message_type.investigation:
                self.icatdb.start_investigation(data)
            else:
                self.icatdb.store_dataset(data)

        if message_type == message_type.addfiles:
            dataset_id = message["datasetId"]
            with self.icatdb.update_dataset(dataset_id) as data:
                if data is None:
                    logger.error("datasetId %s does not exist", dataset_id)
                else:
                    file_count = 0
                    dirname = data["location"]
                    if os.path.exists(dirname):
                        file_count = len(os.listdir(dirname))
                    for parameter in data["parameters"]:
                        if parameter["name"] == "__fileCount":
                            logger.info(
                                "Update file count for %s to %d", dirname, file_count
                            )
                            parameter["value"] = file_count
                            break
                    else:
                        logger.info("Add file count for %s to %d", dirname, file_count)
                        data["parameters"].append(
                            {"name": "__fileCount", "value": file_count}
                        )

        # Notify that data is valid
        if self.s_out is not None:
            self.s_out.sendall(frame.body.encode() + b"\n")


def main(
    host=None, port=60001, queue=None, port_out=0, icat_data_dir: Optional[str] = None
):
    if not host:
        host = "localhost"
    if not queue:
        queue = "/queue/icatIngest"
    conn = stomp.Connection([(host, port)])
    # Listener will run in a different thread
    listener = MyListener(conn, icat_data_dir)
    conn.set_listener("", listener)
    conn.connect("guest", "guest", wait=True)
    conn.subscribe(destination=queue, id=1, ack="auto")
    logger.info(f"subscribed to {queue} on STOMP {host}:{port}")
    if port_out:
        listener.redirect_messages(port_out)
        listener.s_out.sendall(b"LISTENING\n")
    logger.info("CTRL-C to stop")
    try:
        threading.Event().wait()
    finally:
        logger.info("Exit.")


if __name__ == "__main__":
    import argparse

    basic_config(
        logger=logger,
        level=logging.DEBUG,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="STOMP client which subscribes to a STOMP queue and redirect its output to a socket"
    )
    parser.add_argument(
        "--host", default="localhost", type=str, help="STOMP server host"
    )
    parser.add_argument("--port", default=60001, type=int, help="STOMP server port")
    parser.add_argument(
        "--queue", default="/queue/icatIngest", type=str, help="STOMP queue"
    )
    parser.add_argument("--port_out", default=0, type=int, help="output socket")
    parser.add_argument(
        "--icat_data_dir", default=None, type=str, help="Dataset directory"
    )
    args = parser.parse_args()

    main(
        host=args.host,
        port=args.port,
        port_out=args.port_out,
        queue=args.queue,
        icat_data_dir=args.icat_data_dir,
    )
