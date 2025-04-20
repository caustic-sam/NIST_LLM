def test_stomp(icat_publisher, icat_subscriber):
    icat_publisher.sendall(b"MYMESSAGE1\nMYMESSAGE2\n")
    assert icat_subscriber.get(timeout=5) == "MYMESSAGE1"
    assert icat_subscriber.get(timeout=5) == "MYMESSAGE2"


def test_activemq_rest_server(activemq_rest_server):
    # TODO: send test request
    pass


def test_icat_logbook_server(icat_logbook_subscriber):
    # TODO: send test request
    pass
