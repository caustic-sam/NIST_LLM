from collections.abc import Mapping
import numpy


def deep_compare(adict: Mapping, expected: Mapping):
    """using logic of deep update to compare two dictionaries"""
    stack = [(adict, expected)]
    while stack:
        adict, expected = stack.pop(0)
        existing_keys = set(adict.keys())
        expected_keys = set(expected.keys())
        missing_keys = expected_keys - existing_keys
        unexpected_keys = existing_keys - expected_keys
        if missing_keys:
            raise AssertionError(f"missing keys: {missing_keys}")
        if unexpected_keys:
            raise AssertionError(f"unexpected keys: {unexpected_keys}")
        for k, v in expected.items():
            if isinstance(v, Mapping):
                stack.append((adict[k], v))
            elif isinstance(v, numpy.ndarray) and v.size > 1:
                assert adict[k].shape == v.shape
                assert adict[k].dtype == v.dtype
                if adict[k].dtype != object:
                    assert all(
                        numpy.isnan(adict[k].flatten()) == numpy.isnan(v.flatten())
                    )
                    mask = numpy.logical_not(numpy.isnan(v.flatten()))
                    assert all((adict[k].flatten() == v.flatten())[mask])
                else:
                    assert all(adict[k].flatten() == v.flatten())
            elif isinstance(v, (list, tuple)):
                assert list(adict[k]) == list(v)
            else:
                assert adict[k] == v
