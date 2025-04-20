from blisswriter.nexus import devices


def test_devices_shortnamemap():
    assert devices.shortnamemap([]) == {}
    assert devices.shortnamemap(["a:b:c"]) == {"a:b:c": "c"}
    assert devices.shortnamemap(["a:b:c", "a:b:d"]) == {"a:b:c": "c", "a:b:d": "d"}
    assert devices.shortnamemap(["a:b:c", "a:b:c", "a:b:d"]) == {
        "a:b:c": "c",
        "a:b:d": "d",
    }
    assert devices.shortnamemap(["a:b:c", "a:b:d", "c"]) == {
        "a:b:c": "b:c",
        "a:b:d": "d",
        "c": "c",
    }
    assert devices.shortnamemap(["a:b:c", "b:c:d", "b:c"]) == {
        "a:b:c": "a:b:c",
        "b:c:d": "d",
        "b:c": "b:c",
    }
    assert devices.shortnamemap(["a:b:c", "a:c"]) == {"a:b:c": "b:c", "a:c": "a:c"}
    assert devices.shortnamemap(["a:b:c", "b:c"]) == {"a:b:c": "a:b:c", "b:c": "b:c"}
    assert devices.shortnamemap(["b:a", "c:a"]) == {"b:a": "b:a", "c:a": "c:a"}


def test_parse_devices__lima_roi_with_underscore():
    fullname = "lima_simulator:roi_counters:roi__1_min"
    d = {
        fullname: {
            "device_type": "lima",
            "single": None,
        },
    }
    devices.parse_devices(d, True, False)
    assert d[fullname]["data_name"] == "min"
    assert d[fullname]["data_type"] == "min"
    assert d[fullname]["metadata_keys"] == {"roi__1": "selection"}
