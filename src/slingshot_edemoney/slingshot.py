#!/bin/env python3

import argparse
import json
import os
import pty
import snoop

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

CONFIG_LOCATIONS = [f"{str(Path.home())}/.config/remote_interp.json",
                    f"{str(Path.home())}/.remote_interp.json"]
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


def update_config(runtime_config: dict, config: dict, args, remote_host: str, local_source_file_path: str) -> dict:
    try:
        script_cfg = config["script_cfg"][str(local_source_file_path)]
    except KeyError:
        script_cfg = {}

    try:
        host_cfg = config["host_cfg"][remote_host]
    except KeyError:
        host_cfg = {}

    runtime_config = runtime_config | host_cfg | script_cfg

    if args.verbose is not None:
        runtime_config["verbose"] = args.verbose
    if args.prompt:
        runtime_config["prompt"] = args.prompt
    if args.args:
        runtime_config["args"] = args.args
    if args.edit_args:
        edit_args(runtime_config)

    config["script_cfg"][str(local_source_file_path)]["args"] = runtime_config["args"]

    if runtime_config["auto_add_hosts"] and remote_host not in config["host_cfg"]:
        config["host_cfg"][remote_host] = {}

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
                           config: dict):
    if config["verbose"] and "--verbose" not in config["rsync_options"] and "-v" not in config["rsync_options"]:
        config["rsync_options"].append("--verbose")

    sysrsync.run(source=str(local_project_root),
                 destination=str(remote_project_root),
                 destination_ssh=remote_host,
                 options=config["rsync_options"],
                 sync_source_contents=False)


def edit_args(config: dict):
    config["args"] = prompt("args: ", default=config["args"])


def execute_on_remote(remote_source_file_path: str, remote_host: str, config: dict):
    return pty.spawn(["ssh", "-tt", remote_host,
                      "cd", remote_source_file_path.parent,
                      "&&",
                      config["interpreter"], str(remote_source_file_path), config["args"]])


def get_remote_path(local_source_file_path: Path, local_project_root: Path, remote_base_dir: str) -> Path:
    relative_source_file_path = Path(str(local_source_file_path).replace(str(local_project_root.parent), ""))
    remote_source_file_path = Path(f"{remote_base_dir}{relative_source_file_path}")
    return remote_source_file_path


def get_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("source_file")
    parser.add_argument("-r", "--remote_host", action="store_true",
                        default=None,
                        help="host to execute code on")
    parser.add_argument("-a", "--args",
                        default=None,
                        help="command line args to execute script with")
    parser.add_argument("-e", "--edit_args",
                        action="store_true",
                        help="edit command line args")
    parser.add_argument("-p", "--prompt", action="store_true",
                        default=None,
                        help="prompt for entry")
    parser.add_argument("-v", "--verbose",
                        default=None,
                        action="store_true",
                        help="verbose")

    args = parser.parse_args()
    return args


def main():
    args = get_args()
    config, config_file_path = read_config()
    runtime_config = DEFAULT_CONFIG | config["global"]

    local_source_file_path = Path(args.source_file).absolute()
    assert os.path.exists(args.source_file)

    if args.remote_host is not None:
        remote_host = args.remote_host
    elif args.prompt is not None:
        remote_host = select_remote_host(config["host_cfg"])
    else:
        remote_host = config["mru_interpreter"]

    config["mru_interpreter"] = remote_host

    runtime_config = update_config(runtime_config=runtime_config,
                                   config=config,
                                   args=args,
                                   remote_host=remote_host,
                                   local_source_file_path=local_source_file_path)

    local_project_root = find_project_root(file=args.source_file, project_roots=runtime_config["project_roots"])
    remote_project_root = runtime_config["remote_base_dir"]

    if runtime_config["allow_config_updates"]:
        write_config(config_file_path=config_file_path, config=config)

    sync_project_to_remote(remote_host=remote_host,
                           local_project_root=local_project_root,
                           remote_project_root=remote_project_root,
                           config=runtime_config)

    remote_source_file_path = get_remote_path(local_project_root=local_project_root,
                                              local_source_file_path=local_source_file_path,
                                              remote_base_dir=runtime_config["remote_base_dir"])

    execute_on_remote(remote_source_file_path=remote_source_file_path,
                      remote_host=remote_host,
                      config=runtime_config)


if __name__ == "__main__":
    main()
