"""
Application which provide a RPC control to a specific controller name.

.. code-block::

   python remote_controller_app.py BEACON_HOST BEACON_PORT CONTROLLER_NAME

"""

import sys
from bliss.config.conductor import client
from bliss.config.conductor import connection
from bliss.comm.rpc import Server
from bliss.config import static


if __name__ == "__main__":
    beacon_host = sys.argv[1]
    beacon_port = int(sys.argv[2])
    controller_name = sys.argv[3]
    beacon_connection = connection.Connection(beacon_host, beacon_port)
    client._default_connection = beacon_connection
    config = static.get_config()
    controller = config.get(controller_name)

    server = Server(controller)
    server.bind("tcp://0:0")
    port = server._socket.getsockname()[1]
    print(f"{port}")
    server.run()
