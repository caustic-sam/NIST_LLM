import pytest
import base64


def test_elogbook_message_wrong_category(elogbook_client):
    client, messages = elogbook_client
    with pytest.raises(ValueError):
        client.send_message(
            "mycontent",
            message_type="wrongcategory",
            beamline="id00",
            proposal="hg123",
            dataset="datasetname",
        )
    assert messages.empty()


def test_elogbook_message(elogbook_client):
    client, messages = elogbook_client
    client.send_message(
        "mycontent",
        message_type="comment",
        beamline="id00",
        proposal="hg123",
        dataset="datasetname",
    )
    message = messages.get(timeout=10)
    assert message.pop("apikey")
    assert message.pop("creationDate")
    assert message.pop("machine")
    assert message.pop("software").startswith("pyicat-plus")
    expected = {
        "type": "annotation",
        "datasetName": "datasetname",
        "category": "comment",
        "content": [{"format": "plainText", "text": "mycontent"}],
        "investigation": "hg123",
        "instrument": "id00",
    }
    assert message == expected
    assert messages.empty()


@pytest.mark.parametrize(
    "editable", [None, True, False], ids=["noneeditable", "editable", "noteditable"]
)
@pytest.mark.parametrize(
    "formatted", [None, True, False], ids=["noneformatted", "formatted", "notformatted"]
)
@pytest.mark.parametrize(
    "message_type", ["debug", "info", "warning", "error", "comment", "command"]
)
@pytest.mark.parametrize("mimetype", ["text/plain", "text/html"])
def test_elogbook_message_options(
    elogbook_client, editable, formatted, message_type, mimetype
):
    client, messages = elogbook_client

    # See https://confluence.esrf.fr/display/DATAPOLWK/Electronic+Logbook#Summary
    if message_type == "comment":
        if formatted is True and editable in (False, None):
            msg_type = "notification"
        elif editable is False and formatted in (True, None):
            msg_type = "notification"
        else:
            msg_type = "annotation"
    else:
        msg_type = "notification"

    has_warning = editable == formatted and formatted is not None
    if message_type != "comment":
        has_warning |= editable is True or formatted is False

    # Check message sending
    if has_warning:
        with pytest.warns(UserWarning):
            client.send_message(
                "mycontent",
                message_type=message_type,
                beamline="id00",
                proposal="hg123",
                editable=editable,
                formatted=formatted,
                mimetype=mimetype,
            )
    else:
        client.send_message(
            "mycontent",
            message_type=message_type,
            beamline="id00",
            proposal="hg123",
            editable=editable,
            formatted=formatted,
            mimetype=mimetype,
        )
    message = messages.get(timeout=10)
    assert messages.empty()

    # Check message content
    if message_type == "command":
        category = "commandLine"
    elif message_type == "warning":
        category = "error"
    else:
        category = message_type
    assert message["category"] == category

    assert message["type"] == msg_type

    if mimetype == "text/plain":
        format = "plainText"
    else:
        format = "html"
    assert message["content"][0]["format"] == format


def test_ebs_elogbook_message(elogbook_ebs_client):
    client, messages = elogbook_ebs_client

    client.send_message("mycontent", message_type="error")
    message = messages.get(timeout=10)
    assert message.pop("apikey")
    assert message.pop("creationDate")
    assert message.pop("machine")
    expected = {
        "type": "broadcast",
        "source": "ebs",
        "category": "error",
        "software": "testsoft",
        "tag": [{"name": "testtag"}, {"name": "machine"}],
        "content": [{"format": "plainText", "text": "mycontent"}],
    }
    assert message == expected

    client.send_message(
        "mycontent", message_type="comment", tags=["commenttag"], software="mysoft"
    )
    message = messages.get(timeout=10)
    assert message.pop("apikey")
    assert message.pop("creationDate")
    assert message.pop("machine")
    expected = {
        "type": "broadcast",
        "source": "ebs",
        "category": "comment",
        "software": "mysoft",
        "tag": [{"name": "testtag"}, {"name": "machine"}, {"name": "commenttag"}],
        "content": [{"format": "plainText", "text": "mycontent"}],
    }
    assert message == expected

    assert messages.empty()


def test_elogbook_message_beamline_only(elogbook_client):
    client, messages = elogbook_client
    client.send_message("mycontent", message_type="comment", beamline="id00")
    message = messages.get(timeout=10)
    assert message.pop("apikey")
    assert message.pop("creationDate")
    assert message.pop("machine")
    assert message.pop("software").startswith("pyicat-plus")
    expected = {
        "type": "annotation",
        "category": "comment",
        "content": [{"format": "plainText", "text": "mycontent"}],
        "instrument": "id00",
    }
    assert message == expected
    assert messages.empty()


def test_elogbook_data(elogbook_client):
    client, messages = elogbook_client
    client.send_binary_data(
        b"123", mimetype="application/octet-stream", beamline="id00", proposal="hg123"
    )
    message = messages.get(timeout=10)
    assert message.pop("apikey")
    assert message.pop("creationDate")
    assert message.pop("machine")
    assert message.pop("software").startswith("pyicat-plus")
    data = message.pop("base64")
    data = data.replace("data:application/octet-stream;base64,", "")
    assert base64.b64decode(data.encode()) == b"123"
    expected = {"investigation": "hg123", "instrument": "id00"}
    assert message == expected
    assert messages.empty()


def test_elogbook_data_beamline_only(elogbook_client):
    client, messages = elogbook_client
    client.send_binary_data(
        b"123", mimetype="application/octet-stream", beamline="id00"
    )
    message = messages.get(timeout=10)
    assert message.pop("apikey")
    assert message.pop("creationDate")
    assert message.pop("machine")
    assert message.pop("software").startswith("pyicat-plus")
    data = message.pop("base64")
    data = data.replace("data:application/octet-stream;base64,", "")
    assert base64.b64decode(data.encode()) == b"123"
    expected = {"instrument": "id00"}
    assert message == expected
    assert messages.empty()
