import time
import datetime
import socketserver
import logging

from .utils import ReuseAddrTCPServer
from ...utils.log_utils import basic_config

logger = logging.getLogger("ACTIVEMQ REST SERVER")

ICAT_QUEUES = [
    "icatIngest",
    "icatArchiveRestoreStatus",
    "icatUpdateDatasetMetadata",
    "icatDataFiles",
]


class MyTCPRequestHandler(socketserver.StreamRequestHandler):
    def handle(self):
        request = self._read_request()
        expected_requests = list(
            map(
                lambda q: f"GET /api/jolokia/read/org.apache.activemq:type=Broker,brokerName=metadata,destinationType=Queue,destinationName={q}/ConsumerCount".encode(
                    "utf-8"
                ),
                ICAT_QUEUES,
            )
        )
        if len([e for e in expected_requests if e in request]) > 0:
            logger.info(f"Send response to {self.client_address[0]}")
            self._send_response()
        elif request:
            logger.info(f"Unknown request\n {request}")
            raise RuntimeError("Unknown request")

    def _read_request(self):
        buff = bytearray(16384)
        request = b""
        try:
            n = self.rfile.readinto1(buff)
            request = bytes(buff[0:n])
        except Exception as e:
            raise RuntimeError("Error reading request") from e
        return request

    def _send_response(self):
        now = datetime.datetime.now().astimezone()
        t1 = now + datetime.timedelta(hours=5)
        out = (
            b"HTTP/1.1 200 OK\r\nContent-Type: text/plain;charset=UTF-8\r\nCache-Control: no-cache\r\nPragma: no-cache\r\nDate: "
            + now.strftime("%a, %d %b %Y %H:%M:%S GTM").encode()
            + b"\r\nExpires: "
            + t1.strftime("%a, %d %b %Y %H:%M:%S GTM").encode()
            + b'\r\nConnection: close\r\nServer: Jetty(7.6.9.v20130131)\r\n\r\n{"timestamp":'
            + str(int(time.time())).encode()
            + b',"status":200,"request":{"mbean":"org.apache.activemq:brokerName=metadata,destinationName=icatIngest,destinationType=Queue,type=Broker","attribute":"ConsumerCount","type":"read"},"value":6}'
        )
        self.wfile.write(out)


def main(port=8778):
    # Create a TCP Server instance
    aServer = ReuseAddrTCPServer(("localhost", port), MyTCPRequestHandler)

    # Listen forever
    logger.info("CTRL-C to stop")
    try:
        aServer.serve_forever()
    finally:
        logger.info("Exit.")


if __name__ == "__main__":
    import argparse

    basic_config(
        logger=logger,
        level=logging.DEBUG,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    parser = argparse.ArgumentParser(description="ActiveMQ REST server")
    parser.add_argument("--port", default=8778, type=int, help="server port")
    args = parser.parse_args()
    main(port=args.port)
