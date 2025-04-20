import sys


def eprint(*args):
    print(*args, file=sys.stderr, flush=True)
