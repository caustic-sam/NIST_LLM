from ..utils.maxsizedict import MaxSizeDict


def test_maxsizedict():
    adict = MaxSizeDict(maxsize=2)
    adict[1] = None
    adict[2] = None
    adict[3] = None
    assert set(adict) == {2, 3}
