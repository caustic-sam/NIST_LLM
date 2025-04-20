import numpy as np
from blissdata.redis_engine.encoding.json import JsonStreamEncoder


def test_dtype_and_shape():
    encoder = JsonStreamEncoder()
    assert encoder.dtype == np.dtype("object")
    assert encoder.shape == ()


def test_encode_decode():
    encoder = JsonStreamEncoder()
    input = {
        "aaa": {
            "bbb": {"ccc": 1, "ddd": 2},
            "eee": [1.0, 2.0, 3.0, 4.0],
            "fff": None,
        }
    }
    encoded = encoder.encode(input)
    output = encoder.decode(encoded)[0]
    assert output == input
