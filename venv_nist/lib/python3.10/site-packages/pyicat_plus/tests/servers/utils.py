import socketserver


class ReuseAddrTCPServer(socketserver.TCPServer):
    allow_reuse_address = True
