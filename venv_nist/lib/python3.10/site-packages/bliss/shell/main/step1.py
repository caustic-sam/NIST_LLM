# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.

"""
First step of the BLISS main.

Check arguments to know if BLISS have to be bootstraped from tmux or not.
"""

import os
import sys
import subprocess
import warnings
from docopt import docopt, DocoptExit

import bliss  # noqa: F401
from bliss import release
from bliss.config import get_sessions_list
from bliss.config import static
from bliss.config.static import ConfigNode
from bliss.config.conductor import client
from bliss.common import constants
from . import session_files_templates as sft

warnings.filterwarnings("ignore", module="jinja2")

DOCOPTS = """
Usage: bliss [-l | --log-level=<log_level>] [-s <name> | --session=<name>] [--no-tmux] [--debug]
       bliss [-v | --version]
       bliss [-c <name> | --create=<name>]
       bliss [-h | --help]
       bliss [-S | --show-sessions]
       bliss --show-sessions-only

Options:
    -l, --log-level=<log_level>   Log level [default: WARN] (CRITICAL ERROR INFO DEBUG NOTSET)
    -s, --session=<session_name>  Start with the specified session
    -v, --version                 Show version and exit
    -c, --create=<session_name>   Create a new session with the given name
    -h, --help                    Show help screen and exit
    --no-tmux                     Deactivate Tmux usage
    --debug                       Allow debugging with full exceptions and keeping tmux alive after Bliss shell exits
    -S, --show-sessions           Display available sessions and tree of sub-sessions
    --show-sessions-only          Display available sessions names only
"""


def yes_or_no(question):
    answer = input(question + " (yes/no) ").lower().strip()
    print("")
    while not (answer in {"yes", "y"} or answer in {"no", "n"}):
        print("Input yes or no")
        answer = input(question + " (yes/no) ").lower().strip()
        print("")

    return answer[0] == "y"


def print_sessions_list(slist):
    for session in slist:
        print(session)


def print_sessions_and_trees(slist):
    print("Available BLISS sessions are:")
    config = static.get_config()
    for name in slist:
        session = config.get(name)
        session.sessions_tree.show()


def create_session(session_name):
    """
    Creation of skeleton files for a new session:
       sessions/<session_name>.yml
       sessions/<session_name>_setup.py
       sessions/scripts/<session_name>.py

    This method is valid even if config directory is located on
    a remote computer.
    """
    beacon = client.get_default_connection()
    print(
        f"Creating '{session_name}' BLISS session on"
        f"BEACON_HOST={beacon._host}:{beacon._port_number}"
    )

    config = static.get_config()
    config.set_config_db_file("sessions/__init__.yml", "plugin: session\n")

    # <session_name>.yml: config file created as a config Node.
    filename = "sessions/%s.yml" % session_name
    new_session_node = ConfigNode(config.root, filename=filename)
    print(("Creating %s" % filename))
    new_session_node.update(
        {
            "class": "Session",
            "name": session_name,
            "setup-file": "./%s_setup.py" % session_name,
            "config-objects": [],
        }
    )
    new_session_node.save()

    # <session_name>_setup.py: setup file of the session.
    skeleton = sft.xxx_setup_py_template.render(name=session_name)
    filename = "sessions/%s_setup.py" % session_name
    print(("Creating %s" % filename))
    config.set_config_db_file(filename, skeleton)

    # scripts/<session_name>.py: additional python script file.
    skeleton = sft.xxx_py_template.render(name=session_name)
    filename = "sessions/scripts/%s.py" % session_name
    print(("Creating %s" % filename))
    config.set_config_db_file(filename, skeleton)


def main(argv=None) -> int:
    # Parse arguments with docopt : it uses this file (main.py) docstring
    # to define parameters to handle.
    if argv is None:
        argv = sys.argv
    try:
        arguments = docopt(DOCOPTS, argv=argv[1:])
    except DocoptExit:
        print("")
        print("Available BLISS sessions:")
        print("-------------------------")
        print_sessions_list(get_sessions_list())
        print("")
        arguments = docopt(DOCOPTS)

    # Print version
    if arguments["--version"]:
        print(("BLISS version %s" % release.version))
        return 0

    # Display session names and trees
    if arguments["--show-sessions"]:
        print_sessions_and_trees(get_sessions_list())
        return 0

    # Display session names only
    if arguments["--show-sessions-only"]:
        print_sessions_list(get_sessions_list())
        return 0

    # Create session
    if arguments["--create"]:
        session_name = arguments["--create"]
        if session_name in get_sessions_list():
            print(("Session '%s' cannot be created: it already exists." % session_name))
            return 0
        elif session_name[0].isdigit():
            print(f"Invalid session name ({session_name}). Must start with [a-zA-Z_]")
            return 0
        else:
            create_session(session_name)
            # exit ( or launch new session ? )
            return 0

    # check beacon connection
    config = static.get_config()

    # Start a specific session
    if arguments["--session"]:
        session_name = arguments["--session"]
        if session_name not in get_sessions_list():
            print(f"\n'{session_name}' does not seem to be a valid session, ", end="")
            if config.invalid_yaml_files:
                print("it may relate to the following yaml error(s):")
                config.parsing_report()
            else:
                print("exiting.")
            print_sessions_list(get_sessions_list())
            return 0
    else:
        session_name = None

    if (
        arguments["--no-tmux"]
        or sys.platform in ["win32", "cygwin"]
        or session_name is None  # no tmux for DEFAULT session
    ):
        from .step2 import main

        main(
            [
                "bliss",
                session_name,
                arguments["--log-level"][0].upper(),
                "1" if arguments["--debug"] else "0",
            ]
        )
    else:
        if session_name is None:
            session = constants.DEFAULT_SESSION_NAME
        else:
            session = session_name

        from bliss import config

        config_path = os.path.join(os.path.dirname(config.__file__), "tmux.conf")

        win1 = "bliss"
        win2 = "scan"

        uid = os.geteuid()

        # not sure if we should use the user id instead => os.getuid()
        # euid (effective user id) can be different from uid.
        # The difference between the regular UID and the Effective UID is that
        # only the EUID is checked when you do something that requires special access
        #  (such as reading or writing a file, or making certain system calls).
        # The UID indicates the actual user who is performing the action,
        # but it is (usually) not considered when examining permissions.
        # In normal programs they will be the same.
        # Some programs change their EUID to add or subtract from the actions they are allowed to take.
        # A smaller number also change their UID, to effectively "become" another user.

        # to use different tmux servers per session the session name is included in the sock name
        # for the default session there is no tmux at all

        tsock = f"/tmp/bliss_tmux_{session_name}_{uid}.sock"

        ans = subprocess.run(
            ["tmux", "-S", tsock, "has-session", "-t", "=%s" % session],
            capture_output=True,
            text=True,
        )
        # print("stdout = ",ans.stdout,", stderr = ", ans.stderr,", returncode = ", ans.returncode)

        if ans.returncode == 0:
            print(f"Tmux session {session} already exist, joining session...")
        else:
            print(f"Starting new tmux session {session}...")
            ans = subprocess.run(["tmux", "-S", tsock, "start-server"])

            if arguments["--debug"]:
                ans = subprocess.run(
                    [
                        "tmux",
                        "-S",
                        tsock,
                        "-f",
                        config_path,
                        "new-session",
                        "-d",
                        "-s",
                        session,
                        "-n",
                        win1,
                    ]
                )

                sub_cmd = f"{sys.executable} -m bliss.shell.main.step2 {session} {arguments['--log-level'][0]} 1"
                ans = subprocess.run(
                    ["tmux", "-S", tsock, "send-keys", "-t", win1, sub_cmd, "Enter"]
                )

                ans = subprocess.run(
                    ["tmux", "-S", tsock, "new-window", "-d", "-n", win2]
                )

                sub_cmd = (
                    f"{sys.executable} -m bliss.shell.data.start_listener {session}"
                )
                ans = subprocess.run(
                    ["tmux", "-S", tsock, "send-keys", "-t", win2, sub_cmd, "Enter"]
                )

            else:
                ans = subprocess.run(
                    [
                        "tmux",
                        "-S",
                        tsock,
                        "-f",
                        config_path,
                        "new-session",
                        "-d",
                        "-s",
                        session,
                        "-n",
                        win1,
                        sys.executable,
                        "-m",
                        "bliss.shell.main.step2",
                        session,
                        arguments["--log-level"][0],
                    ]
                )

                ans = subprocess.run(
                    [
                        "tmux",
                        "-S",
                        tsock,
                        "new-window",
                        "-d",
                        "-n",
                        win2,
                        sys.executable,
                        "-m",
                        "bliss.shell.data.start_listener",
                        session,
                    ]
                )

        ans = subprocess.run(
            ["tmux", "-S", tsock, "set", "-g", "pane-exited", "kill-session"]
        )
        ans = subprocess.run(["tmux", "-S", tsock, "attach-session", "-t", session])
    return 0


if __name__ == "__main__":
    sys.exit(main())
