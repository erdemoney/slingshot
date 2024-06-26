#!/bin/env python3

import argparse
import json
import os
import pty
import sys

import sysrsync
import git
from pathlib import Path
from prompt_toolkit import prompt
from prompt_toolkit.completion import WordCompleter

"""
ssh prompt
edit run command
prompt option
save config options
auto copy ssh id
interpreter config updates
"""

CONFIG_LOCATIONS = [f"{str(Path.home())}/.config/slingshot.json",
                    f"{str(Path.home())}/.slingshot.json"]
DEFAULT_CONFIG = {
    "interpreter": "python3",
    "project_roots": [],
    "auto_add_hosts": True,
    "rsync_options": ["--archive", "--compress", "--delete"],
    "remote_base_dir": "/tmp",
    "close": True,
    "prompt": False,
    "verbose": False,
    "allow_config_updates": True
}


def read_config() -> tuple[dict, str]:
    config_path = None
    for config_path in CONFIG_LOCATIONS:
        if Path(config_path).is_file():
            config_path = config_path
            break
    assert config_path is not None

    config = None
    with open(config_path) as file:
        config = json.load(file)

    return config, config_path


def write_config(config_file_path: str, config: dict):
    with open(config_file_path, 'w', encoding='utf-8') as file:
        json.dump(config, file, ensure_ascii=False, indent=4)


def update_config(runtime_config: dict, config: dict, args, remote_host: str, local_path: str, module: str | None = None) -> dict:
    config_key = local_path if module is None else f"{local_path}_mod_{module}"
    try:
        host_cfg = config["script_cfg"][remote_host]
    except KeyError:
        host_cfg = {}

    try:
        script_cfg = config["script_cfg"][remote_host]["scripts"][str(config_key)]
    except KeyError:
        script_cfg = {}

    runtime_config = runtime_config | host_cfg | script_cfg

    if args.verbose is not None:
        runtime_config["verbose"] = args.verbose
    if args.prompt:
        runtime_config["prompt"] = args.prompt
    if args.args:
        runtime_config["args"] = args.args
    if args.interpreter:
        runtime_config["interpreter"] = args.interpreter
    if args.edit_args:
        edit_args(runtime_config)

    cur = config
    for key in ["script_cfg", remote_host, "scripts", str(config_key)]:
        if key not in cur:
            cur[key] = {}
        cur = cur[key]

    if "args" in runtime_config:
        config["script_cfg"][remote_host]["scripts"][str(config_key)]["args"] = runtime_config["args"]

    if "interpreter" in runtime_config:
        config["script_cfg"][remote_host]["scripts"][str(config_key)]["interpreter"] = runtime_config["interpreter"]

    if runtime_config["auto_add_hosts"] and remote_host not in config["script_cfg"]:
        config["script_cfg"][remote_host] = {}

    return runtime_config


def find_project_root(file: Path, project_roots: list[str]) -> Path:
    try:
        return Path(git.Repo(file, search_parent_directories=True).git.rev_parse("--show-toplevel"))
    except git.exc.InvalidGitRepositoryError:
        for project_root in project_roots:
            if os.path.abspath(project_root) in os.path.abspath(file):
                return project_root
        return file


def select_remote_host(hosts: list[str]) -> str:
    completer = WordCompleter(hosts, ignore_case=True)
    selected_host = prompt("Select remote host: ", completer=completer)
    return selected_host


def sync_project_to_remote(remote_host: str,
                           local_project_root: Path,
                           remote_project_root: Path,
                           runtime_config: dict):
    if runtime_config["verbose"] and "--verbose" not in runtime_config["rsync_options"] and "-v" not in runtime_config["rsync_options"]:
        runtime_config["rsync_options"].append("--verbose")

    sysrsync.run(source=str(local_project_root),
                 destination=str(remote_project_root),
                 destination_ssh=remote_host,
                 options=runtime_config["rsync_options"],
                 sync_source_contents=False,
                 strict=False)


def edit_args(config: dict):
    config["args"] = prompt("args: ", default=config["args"] if "args" in config else "")


def execute_script_on_remote(remote_source_file_path: str, remote_host: str, runtime_config: dict, test: str | None = None):
    if test:
        remote_source_file_path = Path(f"{remote_source_file_path}::{test}")

    return pty.spawn(["ssh", "-tt", remote_host,
                      "cd", remote_source_file_path.parent,
                      "&&",
                      runtime_config["interpreter"], str(remote_source_file_path), runtime_config["args"] if "args" in runtime_config else ""])


def execute_module_on_remote(remote_dir: str, module_name: str, remote_host: str, runtime_config: dict):
    print(f"{remote_host=} {remote_dir=} {runtime_config['interpreter']=} {module_name=} ")
    return pty.spawn(["ssh", "-tt", remote_host,
                      "cd", remote_dir,
                      "&&",
                      runtime_config["interpreter"], "-m", module_name, runtime_config["args"] if "args" in runtime_config else ""])


def get_remote_path(local_path: Path, local_project_root: Path, remote_base_dir: str) -> Path:
    relative_source_file_path = Path(str(local_path).replace(str(local_project_root.parent), ""))
    remote_source_file_path = Path(f"{remote_base_dir}{relative_source_file_path}")
    return remote_source_file_path


def get_args():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("source_file",
                       nargs="?",
                       help="source file to execute")
    group.add_argument("-m",
                       nargs="?",
                       help="Module to execute")
    parser.add_argument("-r", "--remote_host",
                        action="store_true",
                        help="host to execute code on")
    parser.add_argument("-t", "--test",
                        help="pytest test to run")
    parser.add_argument("-a", "--args",
                        help="command line args to execute script with")
    parser.add_argument("-i", "--interpreter",
                        help="interpreter to execute script with")
    parser.add_argument("-e", "--edit_args",
                        action="store_true",
                        help="edit command line args")
    parser.add_argument("-p", "--prompt",
                        action="store_true",
                        help="prompt for entry")
    parser.add_argument("-v", "--verbose",
                        action="store_true",
                        help="verbose")

    args = parser.parse_args()
    return args


def main():
    args = get_args()
    config, config_file_path = read_config()
    runtime_config = DEFAULT_CONFIG | config["global"]

    local_path = Path(args.source_file).absolute() if args.source_file else Path().resolve().absolute()
    if not local_path.exists():
        sys.exit(f"File `{local_path}` does not exist")

    if args.remote_host:
        remote_host = args.remote_host
    elif args.prompt:
        remote_host = select_remote_host(config["script_cfg"])
    else:
        remote_host = config["mru_interpreter"]

    # NOTE: mru_interpreter should be mru_host
    config["mru_interpreter"] = remote_host

    runtime_config = update_config(runtime_config=runtime_config,
                                   config=config,
                                   args=args,
                                   remote_host=remote_host,
                                   local_path=local_path,
                                   module=args.m)

    local_project_root = find_project_root(file=args.source_file, project_roots=runtime_config["project_roots"])
    remote_project_root = runtime_config["remote_base_dir"]

    if runtime_config["allow_config_updates"]:
        write_config(config_file_path=config_file_path, config=config)

    sync_project_to_remote(remote_host=remote_host,
                           local_project_root=local_project_root,
                           remote_project_root=remote_project_root,
                           runtime_config=runtime_config)

    remote_path = get_remote_path(local_project_root=local_project_root,
                                  local_path=local_path,
                                  remote_base_dir=runtime_config["remote_base_dir"])

    if args.m:
        execute_module_on_remote(remote_dir=remote_path,
                                 remote_host=remote_host,
                                 runtime_config=runtime_config,
                                 module_name=args.m)
    else:
        execute_script_on_remote(remote_source_file_path=remote_path,
                                 remote_host=remote_host,
                                 runtime_config=runtime_config,
                                 test=args.test)


if __name__ == "__main__":
    main()
