import os
import re
import subprocess
import pprint
import difflib
import itertools
from collections import OrderedDict
from typing import Optional, List, Tuple, Any
from concurrent.futures import ThreadPoolExecutor


DIFFER = difflib.Differ()

LINESEPSTR = os.linesep.encode("unicode-escape").decode()


def codefilename(tmpdir) -> str:
    return str(tmpdir.join("test.py"))


def outfilename(tmpdir) -> str:
    return str(tmpdir.join("out.log"))


def errfilename(tmpdir) -> str:
    return str(tmpdir.join("err.log"))


def allfilename(tmpdir) -> str:
    return str(tmpdir.join("all.log"))


def read_file(filename: str) -> str:
    try:
        with open(filename, "r") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def read_files(tmpdir) -> Tuple[str, str, str]:
    return (
        read_file(allfilename(tmpdir)),
        read_file(outfilename(tmpdir)),
        read_file(errfilename(tmpdir)),
    )


def generate_test_script(tmpdir, configlogging: bool = False, **_) -> str:
    lines = ["import sys"]
    if configlogging:
        lines += [
            "import logging",
            "import argparse",
            "from blisswriter.utils import logging_utils",
            "parser = argparse.ArgumentParser()",
            "logging_utils.add_cli_args(parser)",
            "logger = logging.getLogger(__name__)",
            "args = parser.parse_args()",
            "logging_utils.cliconfig(logger, args)",
        ]
    else:
        lines += ["import logging", "logger = logging.getLogger()"]
    lines += [
        'logger.critical("CRITICAL")',
        'logger.error("ERROR")',
        'logger.warning("WARNING")',
        'logger.info("INFO")',
        'logger.debug("DEBUG")',
    ]
    if configlogging:
        lines += [
            'logging_utils.print_out("PRINTOUT")',
            'logging_utils.print_err("PRINTERR")',
        ]
    lines += [
        'print("PRINT")',
        'print("")',
        f'print("A{LINESEPSTR}B{LINESEPSTR}", "C"*10, [1,2,3])',
        f'sys.stdout.write("STDOUTWRITE{LINESEPSTR}")',
        "sys.stdout.flush()",
        f'sys.stderr.write("STDERRWRITE{LINESEPSTR}")',
        "sys.stderr.flush()",
    ]
    filename = codefilename(tmpdir)
    with open(filename, mode="w") as f:
        for line in lines:
            f.write(line + os.linesep)
    return filename


# Expected output of the test script for `expected_std`
EXPECTED_LINES = OrderedDict()
EXPECTED_LINES["CRITICAL"] = "CRITICAL", "FATAL:__main__: "
EXPECTED_LINES["ERROR"] = "ERROR", "ERROR:__main__: "
EXPECTED_LINES["WARNING"] = "WARNING", "WARN :__main__: "
EXPECTED_LINES["INFO"] = "INFO", "INFO :__main__: "
EXPECTED_LINES["DEBUG"] = "DEBUG", "DEBUG:__main__: "
EXPECTED_LINES["PRINTOUT"] = "PRINTOUT", ""
EXPECTED_LINES["PRINTERR"] = "PRINTERR", ""
EXPECTED_LINES["PRINT1"] = "PRINT", ""
EXPECTED_LINES["PRINT2"] = "", ""
EXPECTED_LINES["PRINT3"] = (
    f"A{os.linesep}B{os.linesep} CCCCCCCCCC [1, 2, 3]",
    "",
)
EXPECTED_LINES["STDOUTWRITE"] = "STDOUTWRITE", ""
EXPECTED_LINES["STDERRWRITE"] = "STDERRWRITE", ""

LOGGER_OUT_LINES = ["DEBUG", "INFO"]
LOGGER_ERR_LINES = ["WARNING", "ERROR", "CRITICAL"]  # These have stderr as fallback
LOGGER_LINES = LOGGER_OUT_LINES + LOGGER_ERR_LINES
UTIL_OUT_LINES = ["PRINTOUT"]
UTIL_ERR_LINES = ["PRINTERR"]
UTIL_LINES = UTIL_OUT_LINES + UTIL_ERR_LINES
LINES_NEED_LOGGER = LOGGER_OUT_LINES + UTIL_LINES
STD_OUT_LINES = ["STDOUTWRITE"]
STD_ERR_LINES = ["STDERRWRITE"]
STD_LINES = STD_OUT_LINES + STD_ERR_LINES
PRINT_LINES = ["PRINT1", "PRINT2", "PRINT3"]
OUT_LINES = LOGGER_OUT_LINES + UTIL_OUT_LINES + STD_OUT_LINES + PRINT_LINES
ERR_LINES = LOGGER_ERR_LINES + UTIL_ERR_LINES + STD_ERR_LINES

LOG_LEVELS = LOGGER_LINES


def expected_std(
    outtype: Optional[str] = None,
    file: Optional[bool] = None,
    level: Optional[str] = None,
    std: Optional[bool] = None,
    redirectstd: Optional[bool] = None,
    configlogging: Optional[bool] = None,
    **_,
) -> List[str]:
    """
    :param outtype str: out, err or all
    :param bool file: lines in file or stdout/stderr
    :param level:
    :param bool or tuple std: logging to stdout/stderr enabled
    :param bool or tuple redirectstd: redirect stdout/stderr to dedicated loggers
    """
    lines = []
    if outtype == "out":
        other_stream = ERR_LINES
    elif outtype == "err":
        other_stream = OUT_LINES
    else:
        other_stream = []
        redirectstdout, redirectstderr = redirectstd
    for desc, (msg, prefix) in EXPECTED_LINES.items():
        if desc in other_stream:
            continue
        if configlogging:
            if file:
                # expected log file content
                if other_stream:
                    # out or err log file
                    if not redirectstd:
                        if desc in PRINT_LINES:
                            continue
                        if desc in STD_LINES:
                            continue
                else:
                    # out+err log file
                    if not redirectstdout:
                        if desc in PRINT_LINES:
                            continue
                        if desc in STD_OUT_LINES:
                            continue
                    if not redirectstderr:
                        if desc in STD_ERR_LINES:
                            continue
            else:
                # expected stdout/stderr content
                if not std:
                    if redirectstd:
                        continue
                    if desc in LINES_NEED_LOGGER:
                        continue
            if level_filtered(level, desc):
                # Log level too high
                continue
            msg = prefix + msg
        else:
            # expected stdout/stderr content
            if desc in LINES_NEED_LOGGER:
                continue
            # No logger formatting so no prefix
        lines.append(msg)
    if lines:
        lines.append("")
    return lines


def level_filtered(level: str, desc: str) -> bool:
    levels = LOG_LEVELS
    if desc in levels:
        return desc not in levels[levels.index(level) :]
    return False


def expected_stdout(
    stdout: Optional[bool] = None, redirectstdout: Optional[bool] = None, **kwargs
) -> List[str]:
    return expected_std(outtype="out", std=stdout, redirectstd=redirectstdout, **kwargs)


def expected_stderr(
    stderr: Optional[bool] = None, redirectstderr: Optional[bool] = None, **kwargs
) -> List[str]:
    return expected_std(outtype="err", std=stderr, redirectstd=redirectstderr, **kwargs)


def expected_file(
    logfile: Optional[bool] = None,
    outtype: Optional[str] = None,
    configlogging: Optional[bool] = None,
    stdout: Optional[bool] = None,
    stderr: Optional[bool] = None,
    redirectstdout: Optional[bool] = None,
    redirectstderr: Optional[bool] = None,
    **kwargs,
) -> List[str]:
    if logfile and configlogging:
        if outtype == "out":
            std = stdout
            redirectstd = redirectstdout
        elif outtype == "err":
            std = stderr
            redirectstd = redirectstderr
        else:
            std = stdout, stderr
            redirectstd = redirectstdout, redirectstderr
        return expected_std(
            outtype=outtype,
            configlogging=configlogging,
            file=True,
            std=std,
            redirectstd=redirectstd,
            **kwargs,
        )
    else:
        return []


def expected_fileout(fileout: Optional[bool] = None, **kwargs) -> List[str]:
    return expected_file(outtype="out", logfile=fileout, **kwargs)


def expected_fileerr(fileerr: Optional[bool] = None, **kwargs) -> List[str]:
    return expected_file(outtype="err", logfile=fileerr, **kwargs)


def validate_output(tmpdir, output: str, outtype: str, **kwargs):
    if outtype == "stdout":
        lines = expected_stdout(**kwargs)
    elif outtype == "stderr":
        lines = expected_stderr(**kwargs)
    elif outtype == "fileout":
        lines = expected_fileout(**kwargs)
    elif outtype == "fileerr":
        lines = expected_fileerr(**kwargs)
    elif outtype == "fileall":
        return  # TODO
    lines = os.linesep.join(lines)
    lines = lines.split(os.linesep)
    output = output.split(os.linesep)
    timestamp = r" \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} "
    output = [re.sub(timestamp, ":", s) for s in output]

    if lines != output:
        cmd = " ".join(cliargs(tmpdir, **kwargs))
        errmsg = f"Unexpected {repr(outtype)}"
        errmsg += f"\n Command: {repr(cmd)}"
        errmsg += f"\n\n Options: {pprint.pformat(kwargs, indent=2)}"
        errmsg += "\n\n Difference (-: missing, +: unexpected)"
        errmsg += "\n " + "\n ".join(DIFFER.compare(lines, output))
        raise RuntimeError(errmsg)


def cliargs(
    tmpdir,
    level: Optional[str] = None,
    fileall: Optional[bool] = None,
    fileout: Optional[bool] = None,
    fileerr: Optional[bool] = None,
    stdout: Optional[bool] = None,
    stderr: Optional[bool] = None,
    redirectstdout: Optional[bool] = None,
    redirectstderr: Optional[bool] = None,
    **_,
):
    filename = codefilename(tmpdir)
    args = ["python", filename, "--log=" + level]
    if fileall:
        args.append(f"--logfile={allfilename(tmpdir)}")
    if fileout:
        args.append(f"--logfileout={outfilename(tmpdir)}")
    if fileerr:
        args.append(f"--logfileerr={errfilename(tmpdir)}")
    if not stdout:
        args.append("--nologstdout")
    if not stderr:
        args.append("--nologstderr")
    if redirectstdout:
        args.append("--redirectstdout")
    if redirectstderr:
        args.append("--redirectstderr")
    return args


def generate_output(tmpdir, **kwargs) -> dict:
    lst = cliargs(tmpdir, **kwargs)
    p = subprocess.Popen(lst, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = p.communicate()
    fileall, fileout, fileerr = read_files(tmpdir)
    return {
        "stdout": stdout.decode(),
        "stderr": stderr.decode(),
        "fileall": fileall,
        "fileout": fileout,
        "fileerr": fileerr,
    }


def run_single_test(args: Tuple[List[str], Tuple[Any], Any]) -> None:
    parameters, values, tmpdir = args
    kwargs = dict(zip(parameters, values))
    generate_test_script(tmpdir, **kwargs)
    result = generate_output(tmpdir, **kwargs)
    for outtype, output in result.items():
        validate_output(tmpdir, output, outtype, **kwargs)


def run_test(tmpdir, configlogging: bool = True) -> None:
    choices = {
        "configlogging": (configlogging,),
        "level": LOG_LEVELS,
        "fileout": (False, True),
        "fileerr": (False, True),
        "fileall": (False, True),
        "stdout": (False, True),
        "stderr": (True,),
        "redirectstdout": (False, True),
        "redirectstderr": (False, True),
    }
    parameters = list(choices.keys())

    def args_generator():
        for i, values in enumerate(itertools.product(*choices.values())):
            tmpdiri = tmpdir.join(str(i))
            os.makedirs(str(tmpdiri))
            yield parameters, values, tmpdiri

    with ThreadPoolExecutor(max_workers=100) as pool:
        list(pool.map(run_single_test, args_generator()))


def test_systemlogging(tmpdir):
    run_test(tmpdir, configlogging=False)


def test_logging(tmpdir):
    run_test(tmpdir, configlogging=True)
