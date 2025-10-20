"""A build system for django projects (Maybe others too)."""

import argparse
import ast
import datetime
import importlib.resources
import logging
import os
import shlex
import subprocess
import sys
import time

from jinja2 import Environment

from bou.contrib import (
    Cache,
    Db,
    ProcessAction,
    ProcessState,
    SnapshotManager,
    SubProcess,
    chmod_executable,
    get_is_bare_repo,
    get_ref_sha,
    get_resolved_path_absolute,
    is_pid_alive,
    load_module_from_path,
    time_and_log,
    which_or_raise,
)
from bou.errors import BuildError
from bou.fpi import BuildPlugin, BuildSpec, pm

logger = logging.getLogger("bou")
logging.basicConfig(level=logging.INFO)


def init_build_system_handler(args: argparse.Namespace) -> None:
    """Create an empty build system file."""

    build_file = args.build_file
    path = importlib.resources.files("bou") / "fpi.py"
    bou_ast = ast.parse(path.read_text())

    module_body: list[ast.stmt] = [
        ast.Import(names=[ast.alias(name="logging")]),
        ast.Import(names=[ast.alias(name="pathlib")]),
        ast.ImportFrom(
            module="jinja2",
            names=[ast.alias(name="Environment"), ast.alias(name="FileSystemLoader")],
            level=0,
        ),
        ast.ImportFrom(
            module="bou.fpi",
            names=[ast.alias(name="hookimpl_v1"), ast.alias(name="Config")],
            level=0,
        ),
        ast.ImportFrom(
            module="bou.contrib",
            names=[ast.alias(name="Cache")],
            level=0,
        ),
        ast.Assign(
            targets=[ast.Name(id="logger")],
            value=ast.Call(
                func=ast.Attribute(value=ast.Name(id="logging"), attr="getLogger"),
                args=[ast.Constant(value="bou")],
                keywords=[],
            ),
            lineno=5,
        ),
    ]
    for node in ast.walk(bou_ast):
        match node:
            case ast.ClassDef(name="BuildPlugin"):
                for child in node.body:
                    match child:
                        case ast.FunctionDef(
                            name="pre_build"
                            | "build"
                            | "post_build"
                            | "configure"
                            | "pre_release"
                            | "release"
                            | "post_release"
                        ):
                            child.args.args = [
                                arg for arg in child.args.args if arg.arg != "self"
                            ]
                            if child.name == "configure":
                                child.returns = ast.Name(id="Config")
                                child.body = [
                                    ast.Assign(
                                        targets=[ast.Name(id="build_path")],
                                        value=ast.Call(
                                            func=ast.Attribute(
                                                value=ast.Name(id="pathlib"),
                                                attr="Path",
                                            ),
                                            args=[
                                                ast.Constant(value="/tmp/bou/builds")
                                            ],
                                        ),
                                        lineno=0,
                                    ),
                                    ast.Assign(
                                        targets=[
                                            ast.Attribute(
                                                value=ast.Name(id="jinja_env"),
                                                attr="loader",
                                            )
                                        ],
                                        value=ast.Call(
                                            func=ast.Name(id="FileSystemLoader"),
                                            args=[ast.Name(id="build_path")],
                                        ),
                                        lineno=0,
                                    ),
                                    ast.Return(
                                        value=ast.Call(
                                            func=ast.Name(id="Config"),
                                            keywords=[
                                                ast.keyword(
                                                    arg="build_path",
                                                    value=ast.Name(id="build_path"),
                                                ),
                                            ],
                                        )
                                    ),
                                ]
                            else:
                                child.body = [ast.Expr(ast.Constant(...))]

                            decorator_list = []
                            for decorator in child.decorator_list:
                                match decorator:
                                    case ast.Call(
                                        func=ast.Name(id="hookimpl_v1"),
                                        keywords=[
                                            ast.keyword(arg="tryfirst" | "wrapper")
                                        ],
                                    ):
                                        func = decorator.func
                                        decorator_list.append(func)

                            child.decorator_list = decorator_list

                            module_body.append(child)
                        case _:
                            continue
                break
            case _:
                continue

    code = ast.unparse(ast.Module(module_body))
    build_file.write_text(code)

    logger.info(f"Generated build system {build_file}")

    try:
        # Try and format the output with ruff
        cwd = None
        environ = {}
        description = "Formatting generated code with ruff"
        error_prefix = "Failed to reformat generated file with ruff "
        command = shlex.split(f"ruff format {build_file}")

        process = SubProcess(
            description=description,
            command=command,
            environ=environ,
            cwd=cwd,
            error_prefix=error_prefix,
        )
        process.run()
    except FileNotFoundError:
        pass


def install_handler(args: argparse.Namespace) -> None:
    """Install a build file as git hook."""

    build_file_path = args.build_file_path
    bou_cli_path = args.bou_cli_path
    repo_path = args.repo_path
    builds_path = args.builds_path
    db_path = args.db_path
    install_path = repo_path / "hooks/post-receive"

    if not build_file_path.exists():
        logger.error(f"Build file {build_file_path} does not exist.")
        sys.exit(1)

    hook_path = importlib.resources.files("bou") / "post-receive"
    hook_content = hook_path.read_text()

    jinja_env = Environment()
    hook_template = jinja_env.from_string(hook_content)

    # TODO @feature add different strategies i.e. release on tag, qa etc
    rendered_hook_template = hook_template.render(
        bou_cli_path=bou_cli_path,
        repo_path=repo_path,
        builds_path=builds_path,
        db_path=db_path,
        build_file_path=build_file_path,
    )

    install_path.write_text(rendered_hook_template)
    chmod_executable(path=install_path)


def build_handler(args: argparse.Namespace) -> None:
    """Run the build process."""

    ref = args.ref
    user = args.user
    builds_path = args.builds_path
    repo_path = args.repo_path
    db_path = args.db_path
    build_file_path = args.build_file_path
    schedule_release = args.schedule_release
    current_datetime = datetime.datetime.now(datetime.UTC)
    ref_sha = get_ref_sha(repo_path=repo_path, ref=ref, environ={})
    pid = os.getpid()
    jinja_env = Environment()

    if not repo_path.exists():
        logger.error("Repository does not exist.")
        sys.exit(1)

    git_repo_is_bare = get_is_bare_repo(repo_path=repo_path, environ={})

    if not git_repo_is_bare:
        logger.error(
            f"Repository does not exist, did you run 'git -C {repo_path} init --bare'?."
        )
        sys.exit(1)

    if not builds_path.exists():
        logger.error(f"Builds path does not exist '{builds_path}'.")
        sys.exit(1)

    db = Db.init_with_defaults(db_path=db_path)
    cache = Cache(db)
    snapshot_manager = SnapshotManager(db)

    # Load the build system
    plugin_module = load_module_from_path(build_file_path)
    pm.add_hookspecs(BuildSpec)
    pm.register(BuildPlugin())
    pm.register(plugin_module)
    pm.check_pending()

    snapshot = snapshot_manager.get(ref_sha=ref_sha)

    if not snapshot:
        snapshot = snapshot_manager.create(
            ref=ref,
            ref_sha=ref_sha,
            action=ProcessAction.BUILD,
            state=ProcessState.RUNNING,
            current_datetime=current_datetime,
            user=user,
            pid=pid,
        )
        # If we are unable to create a snapshot another process beat us to it
        if not snapshot:
            message = (
                f"Unable to create build snapshot for '{ref_sha}', "
                " another process beat you too it. "
                "process will exit shortly"
            )
            logger.info(message)
            sys.exit(0)

    # A pid could be the same as a historical one that is dead, so always check if the snapshot pid is alive
    snapshot_pid_is_alive = is_pid_alive(snapshot.pid)

    if pid != snapshot.pid and snapshot_pid_is_alive:
        message = (
            f"An existing {snapshot.action} is running for '{ref_sha}' please try again when it completes, "
            "process will exit shortly"
        )
        logger.info(message)
        sys.exit(0)
    elif pid != snapshot.pid and not snapshot_pid_is_alive:
        snapshot = snapshot_manager.adopt_into_running_state(
            snapshot=snapshot,
            action=ProcessAction.BUILD,
            current_datetime=current_datetime,
            user=user,
            pid=pid,
        )

        if not snapshot:
            message = (
                f"Optimistic lock acquisition race lost for snapshot '{ref_sha}', "
                " another process beat you too it. "
                "process will exit shortly"
            )
            logger.info(message)
            sys.exit(0)
        else:
            logger.info(
                f"Optimistic lock acquisition race won for snapshot '{ref_sha}', build will begin shortly"
            )

    if snapshot.action != ProcessAction.BUILD:
        message = (
            f"An existing {snapshot.action} is running for '{ref_sha}' please try again when it completes, "
            "process will exit shortly"
        )
        logger.info(message)
        sys.exit(0)

    with time_and_log(message_prefix="Configuration execution time "):
        config = pm.hook.configure(
            ref=ref,
            ref_sha=ref_sha,
            builds_path=builds_path,
            repo_path=repo_path,
            jinja_env=jinja_env,
            cache=cache,
        )

    build_path = config["build_path"]

    with time_and_log(message_prefix="Pre-build execution time "):
        pm.hook.pre_build(
            ref=ref,
            ref_sha=ref_sha,
            repo_path=repo_path,
            builds_path=builds_path,
            build_path=build_path,
            jinja_env=jinja_env,
            config=config,
            cache=cache,
        )
    with time_and_log(message_prefix="Build execution time "):
        pm.hook.build(
            ref=ref,
            ref_sha=ref_sha,
            repo_path=repo_path,
            builds_path=builds_path,
            build_path=build_path,
            jinja_env=jinja_env,
            config=config,
            cache=cache,
        )
    with time_and_log(message_prefix="Post-build execution time "):
        pm.hook.post_build(
            ref=ref,
            ref_sha=ref_sha,
            repo_path=repo_path,
            builds_path=builds_path,
            build_path=build_path,
            jinja_env=jinja_env,
            config=config,
            cache=cache,
        )

    if not schedule_release:
        snapshot_manager.complete(
            snapshot=snapshot,
            current_datetime=current_datetime,
            user=user,
        )
        sys.exit(0)

    else:
        snapshot = snapshot_manager.schedule_for_release(
            snapshot=snapshot,
            current_datetime=current_datetime,
            user=user,
        )

        logger.info(f"Snapshot '{ref_sha}' scheduled for release")


def release_handler(args: argparse.Namespace) -> None:
    """Run the release process.

    Only one release can run at a time.

    This handler makes use of a non blocking advisory lock. If unable to acquire
    the lock the process will log and exit. When requests for concurrent releases
    are made it is best to handle them with an external release server which processes
    releases sequentially.
    """

    ref = args.ref
    user = args.user
    build_file_path = args.build_file_path
    builds_path = args.builds_path
    repo_path = args.repo_path
    db_path = args.db_path
    pid = os.getpid()
    jinja_env = Environment()
    ref_sha = get_ref_sha(repo_path=repo_path, ref=ref, environ={})
    current_datetime = datetime.datetime.now(datetime.UTC)

    if not repo_path.exists():
        logger.error("Repository does not exist.")
        sys.exit(1)

    git_repo_is_bare = get_is_bare_repo(repo_path=repo_path, environ={})

    if not git_repo_is_bare:
        logger.error(
            f"Repository does not exist, did you run 'git -C {repo_path} init --bare'?."
        )
        sys.exit(1)

    if not builds_path.exists():
        logger.error(f"Builds path does not exist '{builds_path}'.")
        sys.exit(1)

    # Load the build system
    plugin_module = load_module_from_path(build_file_path)
    pm.add_hookspecs(BuildSpec)
    pm.register(BuildPlugin())
    pm.register(plugin_module)
    pm.check_pending()

    db = Db.init_with_defaults(db_path=db_path)
    cache = Cache(db)
    snapshot_manager = SnapshotManager(db)

    snapshot = snapshot_manager.get(ref_sha=ref_sha)

    if not snapshot:
        snapshot = snapshot_manager.create(
            ref=ref,
            ref_sha=ref_sha,
            action=ProcessAction.BUILD,
            state=ProcessState.RUNNING,
            current_datetime=current_datetime,
            user=user,
            pid=pid,
        )
        # If we are unable to create a snapshot another process beat us to it
        if not snapshot:
            message = (
                f"Unable to create release snapshot for '{ref_sha}', "
                " another process beat you too it. "
                "process will exit shortly"
            )
            logger.info(message)
            sys.exit(0)

    # A pid could be the same as a historical one that is dead, so always check if the snapshot pid is alive
    snapshot_pid_is_alive = is_pid_alive(snapshot.pid)

    if pid != snapshot.pid and snapshot_pid_is_alive:
        message = (
            f"An existing {snapshot.action} is running for '{ref_sha}' please try again when it completes, "
            "process will exit shortly"
        )
        logger.info(message)
        sys.exit(0)
    elif pid != snapshot.pid and not snapshot_pid_is_alive:
        snapshot = snapshot_manager.adopt_into_running_state(
            snapshot=snapshot,
            action=ProcessAction.RELEASE,
            current_datetime=current_datetime,
            user=user,
            pid=pid,
        )

        if not snapshot:
            message = (
                f"Optimistic lock acquisition race lost for snapshot '{ref_sha}', "
                " another process beat you too it. "
                "process will exit shortly"
            )
            logger.info(message)
            sys.exit(0)
        else:
            logger.info(
                f"Optimistic lock acquisition race won for snapshot '{ref_sha}', build will begin shortly"
            )

    if snapshot.action != ProcessAction.RELEASE:
        message = (
            f"An existing {snapshot.action} is running for '{ref_sha}' please try again when it completes, "
            "process will exit shortly"
        )
        logger.info(message)
        sys.exit(0)

    with time_and_log(message_prefix="Configuration execution time "):
        config = pm.hook.configure(
            ref=ref,
            ref_sha=ref_sha,
            builds_path=builds_path,
            repo_path=repo_path,
            jinja_env=jinja_env,
            cache=cache,
        )

    build_path = config["build_path"]

    if not build_path.exists():
        logger.error(f"Build for {ref_sha} does not exist.")
        sys.exit(1)

    with time_and_log(message_prefix="Pre-release execution time "):
        pm.hook.pre_release(
            ref=ref,
            ref_sha=ref_sha,
            repo_path=repo_path,
            builds_path=builds_path,
            build_path=build_path,
            jinja_env=jinja_env,
            config=config,
            cache=cache,
        )

    with time_and_log(message_prefix="Release execution time "):
        pm.hook.release(
            ref=ref,
            ref_sha=ref_sha,
            repo_path=repo_path,
            builds_path=builds_path,
            build_path=build_path,
            jinja_env=jinja_env,
            config=config,
            cache=cache,
        )

    with time_and_log(message_prefix="Post-release execution time "):
        pm.hook.post_release(
            ref=ref,
            ref_sha=ref_sha,
            repo_path=repo_path,
            builds_path=builds_path,
            build_path=build_path,
            jinja_env=jinja_env,
            config=config,
            cache=cache,
        )

    # Set the snapshot as completed
    snapshot_manager.complete(
        snapshot=snapshot,
        current_datetime=current_datetime,
        user=user,
    )


def db_handler(args: argparse.Namespace) -> None:
    table = args.table
    limit = args.limit
    order = args.order
    query = args.query
    refresh = args.refresh
    db_path = args.db_path

    # Initialize the database if necessary
    Db.init_with_defaults(db_path=db_path)

    sqlite_path = which_or_raise("sqlite3")

    if not query:
        sql = f"SELECT * FROM {table}\n"
    else:
        sql = f"""
        SELECT * FROM {table}\n WHERE ref_sha LIKE '%{query}%'
        """

    if order:
        sql += f"ORDER BY created_at {order}\n"

    if limit:
        sql += f"LIMIT {limit}"

    command = shlex.split(f'{sqlite_path} -box {db_path} "{sql}"')

    if refresh:
        while True:
            result = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
            )
            sys.stdout.write(result.stdout)
            sys.stdout.flush()

            try:
                time.sleep(refresh)
            except KeyboardInterrupt:
                break

            os.system("clear")
    else:
        subprocess.run(
            command,
            check=True,
        )


def add_common_parse_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-B",
        "--build-file",
        dest="build_file_path",
        type=get_resolved_path_absolute,
        help="The path to a bou build implementation.",
        required=True,
    )
    parser.add_argument(
        "-b",
        "--builds-path",
        dest="builds_path",
        type=get_resolved_path_absolute,
        help="The path to builds folder.",
        required=True,
    )
    parser.add_argument(
        "-d",
        "--db-path",
        dest="db_path",
        type=get_resolved_path_absolute,
        help="The path to the bou database.",
        required=True,
    )
    parser.add_argument(
        "-r",
        "--repo-path",
        dest="repo_path",
        type=get_resolved_path_absolute,
        help="The path to the git repository.",
        required=True,
    )


def main() -> None:
    """CLI entry-point."""

    allowed_log_levels = [
        "CRITICAL",
        "ERROR",
        "WARNING",
        "INFO",
        "DEBUG",
        "NOTSET",
    ]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-l",
        "--log-level",
        dest="log_level",
        choices=allowed_log_levels,
        default="INFO",
    )
    subparsers = parser.add_subparsers(title="Bou", required=True)

    # Init
    init_parser = subparsers.add_parser("init", help="Initialize a new build system.")
    init_parser.add_argument(
        "build_file",
        type=get_resolved_path_absolute,
        help="A path where the build file will be saved.",
    )
    init_parser.set_defaults(handler=init_build_system_handler)

    # Install
    install_parser = subparsers.add_parser(
        "install", help="Install the build process as a git post-receive hook."
    )
    install_parser.add_argument(
        "-c",
        "--cli-path",
        dest="bou_cli_path",
        type=get_resolved_path_absolute,
        help="The path to the bou CLI.",
        required=True,
    )
    add_common_parse_args(install_parser)
    install_parser.set_defaults(handler=install_handler)

    # Build
    build_parser = subparsers.add_parser("build", help="Run the build process.")
    add_common_parse_args(build_parser)

    build_parser.add_argument("ref", help="Git reference to git commit to be built.")
    build_parser.add_argument("user", help="Name of the user running the build.")
    build_parser.add_argument(
        "-sr",
        "--schedule-release",
        dest="schedule_release",
        action="store_true",
        help="Create a release record in the bou db in a SCHEDULED state.",
    )
    build_parser.set_defaults(handler=build_handler)

    # Release
    release_parser = subparsers.add_parser("release", help="Run the release process.")
    add_common_parse_args(release_parser)
    release_parser.add_argument("ref", help="Git reference to commit to be released.")
    release_parser.add_argument("user", help="Name of the user running the release.")
    release_parser.set_defaults(handler=release_handler)

    # Db
    db_parser = subparsers.add_parser("db", help="Query the bou database.")
    db_parser.add_argument(
        "-d",
        "--db-path",
        dest="db_path",
        type=get_resolved_path_absolute,
        help="The path to the bou database.",
        required=True,
    )
    db_parser.add_argument("table", choices=["snapshot", "snapshot_history"])
    db_parser.add_argument("-l", "--limit", type=int)
    db_parser.add_argument("-o", "--order", choices=["asc", "desc"])
    db_parser.add_argument("-q", "--query")
    db_parser.add_argument("-r", "--refresh", type=float, default=0)
    db_parser.set_defaults(handler=db_handler)

    args = parser.parse_args()

    logger.setLevel(args.log_level)

    try:
        with time_and_log(message_prefix="Total execution time "):
            args.handler(args=args)

    except BuildError as ex:
        logger.error(f"{ex}")
        sys.exit(1)
