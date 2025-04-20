import pytest

from blisswriter.io.io_utils import rotatefiles


@pytest.mark.parametrize("nmax", [1, 3])
def test_rotating_file(nmax, tmpdir):
    n = nmax + 2
    for i in range(n):
        filename = str(tmpdir.join("logfile.log"))
        rotatefiles(filename, nmax=nmax)
        with open(filename, mode="a") as fp:
            fp.write(str(i))
    for i in range(nmax):
        if i:
            filename = tmpdir.join(f"logfile.{i}.log")
        else:
            filename = tmpdir.join("logfile.log")
        with open(str(filename), mode="r") as fp:
            assert fp.read() == str(n - 1 - i)
