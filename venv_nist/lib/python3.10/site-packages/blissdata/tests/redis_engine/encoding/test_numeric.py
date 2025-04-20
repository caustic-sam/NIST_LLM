import pytest
import sys
import numpy as np

from blissdata.redis_engine.encoding.numeric import NumericStreamEncoder


def test_dtype_validation():
    numeric_types = [
        bool,
        np.uint8,
        np.uint16,
        np.uint32,
        np.uint64,
        int,  # same as np.int64
        np.int8,
        np.int16,
        np.int32,
        np.int64,
        float,  # same as np.float64
        np.float16,
        np.float32,
        np.float64,
        np.float128,
        np.complex64,
        np.complex128,
    ]

    for encoder_type in numeric_types:
        encoder = NumericStreamEncoder(dtype=encoder_type)
        assert encoder.dtype == encoder_type
        for data_type in numeric_types:
            value = data_type(0)
            if np.dtype(data_type) == np.dtype(encoder_type):
                encoder.encode(value)
            else:
                with pytest.raises(TypeError) as exc_info:
                    encoder.encode(value)
                assert "Expected numpy." in exc_info.value.args[0]


def test_non_numeric_exclusion():
    for non_numeric in [str, bytes, list, dict, set, tuple, object]:
        with pytest.raises(ValueError):
            NumericStreamEncoder(dtype=non_numeric)


def test_endianness_validation():
    native_endian = {"big": ">", "little": "<"}[sys.byteorder]
    reversed = {"<": ">", ">": "<"}
    non_native_encoder = NumericStreamEncoder(dtype=reversed[native_endian] + "f8")

    with pytest.raises(TypeError) as exc_info:
        non_native_encoder.encode(np.float64(1.2345))
    assert "endian" in exc_info.value.args[0]


@pytest.mark.parametrize("shape", [(), (2,), (2, 3), (-1,), (-1, -1)])
def test_empty_data(shape):
    encoder = NumericStreamEncoder(dtype=float, shape=shape)
    assert encoder.shape == shape
    with pytest.raises(ValueError) as exc_info:
        encoder.encode([])
    assert "empty data" in exc_info.value.args[0]


@pytest.mark.parametrize(
    # fmt: off
    "is_valid, single_point_shape, data_shapes",
    [
        # Scalar
        (True, (), [()]),
        (True, (), [(1, )]),
        (True, (), [(3, )]),
        (False, (), [(1, 1)]),
        (False, (), [(3, 1)]),
        (False, (), [(1, 3)]),

        # 1D
        (True, (3, ), [(3, )]),
        (True, (3, ), [(1, 3)]),
        (True, (3, ), [(7, 3)]),
        (False, (3, ), [()]),
        (False, (3, ), [(2, )]),
        (False, (3, ), [(4, )]),
        (False, (3, ), [(6, )]),
        (False, (3, ), [(3, 1)]),
        (False, (3, ), [(1, 1, 3)]),

        # 1D variable length
        (True, (-1, ), [(1, )]),
        (True, (-1, ), [(7, )]),
        (True, (-1, ), [(1, 5)]),
        (False, (-1, ), [()]),
        (True, (-1, ), [(2, 5)]),
        (True, (-1, ), [(5, 1)]),

        # 2D
        (True, (3, 2), [(3, 2)]),
        (True, (3, 2), [(1, 3, 2)]),
        (True, (3, 2), [(7, 3, 2)]),
        (False, (3, 2), [()]),
        (False, (3, 2), [(3)]),
        (False, (3, 2), [(2)]),
        (False, (3, 2), [(2, 2)]),
        (False, (3, 2), [(3, 3)]),
        (False, (3, 2), [(6, 2)]),
        (False, (3, 2), [(3, 4)]),
        (False, (3, 2), [(3, 2, 1)]),
        (False, (3, 2), [(1, 1, 3, 2)]),

        # 2D variable length
        (True, (3, -1), [(3, 1)]),
        (True, (3, -1), [(3, 7)]),
        (True, (3, -1), [(1, 3, 7)]),
        (False, (3, -1), [(3, )]),
        (True, (3, -1), [(2, 3, 7)]),
        (False, (3, -1), [(3, 7, 1)]),

        (True, (-1, 3), [(1, 3)]),
        (True, (-1, 3), [(7, 3)]),
        (True, (-1, 3), [(1, 7, 3)]),
        (False, (-1, 3), [(3, )]),
        (True, (-1, 3), [(2, 7, 3)]),
        (False, (-1, 3), [(7, 3, 1)]),

        (True, (-1, -1), [(1, 3)]),
        (True, (-1, -1), [(3, 1)]),
        (True, (-1, -1), [(3, 7)]),
        (True, (-1, -1), [(1, 7, 3)]),
        (True, (-1, -1), [(5, 7, 3)]),
        (True, (-1, -1), [(1, 3), (1, 7)]),
        (False, (-1, -1), [()]),
        (False, (-1, -1), [(3, )]),
        (False, (-1, -1), [(1, 5, 7, 3)]),
        (False, (-1, -1), [(1, 3), (2, )]),

        # higher dimension should work the same...
    ],
    # fmt: on
)
def test_shape_validation(is_valid, single_point_shape, data_shapes):
    encoder = NumericStreamEncoder(dtype=int, shape=single_point_shape)
    decoder = NumericStreamEncoder.from_info(encoder.info())

    encode_inputs = list()
    expected_ouputs = list()
    for data_shape in data_shapes:
        nb_elts = np.prod(data_shape, dtype=int)
        linear_data = np.arange(nb_elts)
        linear_data = linear_data.reshape(data_shape)
        encode_inputs.append(linear_data)
        if linear_data.ndim <= len(single_point_shape):
            expected_ouputs.append(linear_data)
        else:
            expected_ouputs.extend(linear_data)

    if not is_valid:
        with pytest.raises(ValueError) as exc_info:
            encoded_batches = [encoder.encode(input) for input in encode_inputs]
        assert "Expected shape" in exc_info.value.args[0]
        return

    encoded_batches = [encoder.encode(input) for input in encode_inputs]
    output = decoder.decode(encoded_batches)

    assert len(expected_ouputs) == len(output)
    for edata, odata in zip(expected_ouputs, output):
        assert np.array_equal(edata, odata)


def test_multiple_batch_decoding():
    encoder = NumericStreamEncoder(dtype=float, shape=(-1, -1))
    decoder = NumericStreamEncoder.from_info(encoder.info())

    # homogenous batch shapes
    shape = (2, 3)
    batches = [encoder.encode(np.empty(shape)) for _ in range(5)]
    output = decoder.decode(batches)
    assert np.array_equal(output.shape, (5, 2, 3))

    # heterogenous batch shapes
    shapes = [(2, 3), (2, 8, 1), (4, 7)]
    expected_shapes = [(2, 3), (8, 1), (8, 1), (4, 7)]
    batches = [encoder.encode(np.empty(shape)) for shape in shapes]
    output = decoder.decode(batches)
    with pytest.raises(AttributeError):
        output.shape
    assert len(output) == len(expected_shapes)
    for point, expected_shape in zip(output, expected_shapes):
        assert np.array_equal(point.shape, expected_shape)
