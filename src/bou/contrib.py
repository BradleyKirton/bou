"""Bou utility functions."""

import contextlib
import datetime
import enum
import errno
import fcntl
import hashlib
import importlib.util
import logging
import os
import pathlib
import shlex
import shutil
import sqlite3
import stat
import subprocess
import textwrap
import time
import typing as t
import urllib.request
from types import ModuleType

from jinja2 import Environment

from bou.errors import BuildError, DependencyError

logger = logging.getLogger(__name__)


class ProcessAction(enum.StrEnum):
    """Models process actions."""

    BUILD = enum.auto()
    RELEASE = enum.auto()


class ProcessState(enum.StrEnum):
    """Models process states."""

    SCHEDULED = enum.auto()
    RUNNING = enum.auto()
    COMPLETE = enum.auto()


class Db:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    @staticmethod
    def datetime_converter(value: bytes) -> datetime.datetime:
        return datetime.datetime.fromisoformat(value.decode())

    @classmethod
    def init_with_defaults(cls, db_path: pathlib.Path) -> "Db":
        conn = sqlite3.connect(db_path)
        sqlite3.register_converter("DATETIME", Db.datetime_converter)

        commit_sql = """
        CREATE TABLE IF NOT EXISTS snapshot (
            ref TEXT,
            ref_sha TEXT PRIMARY KEY,
            action TEXT,
            state TEXT,
            created_at DATETIME,
            created_by TEXT,
            updated_at DATETIME NULL,
            updated_by TEXT NULL,
            pid INTEGER,
            efd DATETIME,
            etd DATETIME NULL
        )
        """
        commit_history_sql = """
        CREATE TABLE IF NOT EXISTS snapshot_history (
            ref TEXT,
            ref_sha TEXT,
            action TEXT,
            state TEXT,
            created_at DATETIME,
            created_by TEXT,
            updated_at DATETIME NULL,
            updated_by TEXT NULL,
            pid INTEGER,
            efd DATETIME,
            etd DATETIME
        )
        """
        cache_sql = """
        CREATE TABLE IF NOT EXISTS cache (
            key TEXT,
            value JSON,
            CONSTRAINT ucon_cache UNIQUE(key)
        )
        """

        with conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA temp_store = MEMORY")

            conn.execute(commit_sql)
            conn.execute(commit_history_sql)
            conn.execute(cache_sql)

        return cls(conn)

    @staticmethod
    def row_factory_wrapper[T](
        model_class: type[T],
    ) -> t.Callable[[sqlite3.Cursor, sqlite3.Row], T]:
        def wrapper(cursor: sqlite3.Cursor, record: sqlite3.Row) -> T:
            names = [column[0] for column in cursor.description]
            kwargs = dict(zip(names, record))
            return model_class(**kwargs)

        return wrapper

    def execute(
        self,
        sql: str,
        params: dict[str, t.Any] | None = None,
    ) -> int:
        if not params:
            params = {}
        with self.conn:
            cursor = self.conn.execute(sql, params)
            rowcount = cursor.rowcount
        return rowcount

    def execute_and_fetchone[T](
        self,
        sql: str,
        model_class: type[T],
        params: dict[str, t.Any] | None = None,
    ) -> T:
        if not params:
            params = {}

        with self.conn:
            cursor = self.conn.execute(sql, params)
            cursor.row_factory = self.row_factory_wrapper(model_class)
            instance = cursor.fetchone()

        return instance

    def execute_and_fetchmany[T](
        self,
        sql: str,
        size: int,
        model_class: type[T],
        params: dict[str, t.Any] | None = None,
    ) -> list[T]:
        if not params:
            params = {}

        with self.conn:
            cursor = self.conn.execute(sql, params)
            cursor.row_factory = self.row_factory_wrapper(model_class)
            instances = cursor.fetchmany(size=size)
        return instances

    def execute_and_fetchall[T](
        self,
        sql: str,
        model_class: type[T],
        params: dict[str, t.Any] | None = None,
    ) -> list[T]:
        if not params:
            params = {}

        with self.conn:
            cursor = self.conn.execute(sql, params)
            cursor.row_factory = self.row_factory_wrapper(model_class)
            instances = cursor.fetchall()
        return instances


Snapshot = t.NamedTuple(
    "Snapshot",
    [
        ("ref", str),
        ("ref_sha", str),
        ("action", ProcessAction),
        ("state", ProcessState),
        ("created_at", datetime.datetime),
        ("created_by", str),
        ("updated_at", datetime.datetime),
        ("updated_by", str),
        ("pid", int),
        ("efd", datetime.datetime),
        ("etd", datetime.datetime),
    ],
)


class SnapshotManager:
    def __init__(self, db: Db) -> None:
        self.db = db

    def list(self, size: int) -> list[Snapshot]:
        return self.db.execute_and_fetchmany(
            sql="SELECT rowid, * FROM release", size=size, model_class=Snapshot
        )

    def get(self, ref_sha: str) -> Snapshot | None:
        """Get a snapshot for the ref_sha."""
        sql = """
        SELECT *
        FROM snapshot
        WHERE ref_sha = :ref_sha
        """
        params = {"ref_sha": ref_sha}
        return self.db.execute_and_fetchone(
            sql=sql, model_class=Snapshot, params=params
        )

    def adopt_into_running_state(
        self,
        snapshot: Snapshot,
        action: ProcessAction,
        current_datetime: datetime.datetime,
        user: str,
        pid: int,
    ) -> Snapshot | None:
        """Attempt to adopt a commit optimistically."""

        sql = """
        UPDATE snapshot SET
            action = :action,
            state = :running_state,
            updated_at = :updated_at,
            updated_by = :updated_by,
            pid = :pid
        WHERE ref_sha = :ref_sha
            AND state = :state
        RETURNING *
        """

        ref_sha = snapshot.ref_sha
        state = snapshot.state

        params = {
            "ref_sha": ref_sha,
            "action": action,
            "running_state": ProcessState.RUNNING,
            "state": state,
            "updated_at": current_datetime,
            "updated_by": user,
            "pid": pid,
        }
        return self.db.execute_and_fetchone(
            sql=sql, model_class=Snapshot, params=params
        )

    def complete(
        self,
        snapshot: Snapshot,
        current_datetime: datetime.datetime,
        user: str,
    ) -> Snapshot | None:
        """Mark the snapshot as completed."""

        sql = """
        UPDATE snapshot SET
            state = :complete_state,
            updated_at = :updated_at,
            updated_by = :updated_by,
            efd = :efd
        WHERE ref_sha = :ref_sha
            AND state = :state
        RETURNING *
        """

        ref_sha = snapshot.ref_sha
        state = snapshot.state

        params = {
            "ref_sha": ref_sha,
            "complete_state": ProcessState.COMPLETE,
            "state": state,
            "updated_at": current_datetime,
            "updated_by": user,
            "efd": current_datetime,
        }
        return self.db.execute_and_fetchone(
            sql=sql, model_class=Snapshot, params=params
        )

    def schedule_for_release(
        self,
        snapshot: Snapshot,
        current_datetime: datetime.datetime,
        user: str,
    ) -> Snapshot | None:
        """Schedule the snapshot for release."""

        sql = """
        UPDATE snapshot SET
            action = :release_action,
            state = :scheduled_state,
            updated_at = :updated_at,
            updated_by = :updated_by,
            efd = :efd
        WHERE ref_sha = :ref_sha
            AND state = :state
        RETURNING *
        """

        ref_sha = snapshot.ref_sha
        state = snapshot.state

        params = {
            "ref_sha": ref_sha,
            "release_action": ProcessAction.RELEASE,
            "scheduled_state": ProcessState.SCHEDULED,
            "state": state,
            "updated_at": current_datetime,
            "updated_by": user,
            "efd": current_datetime,
        }
        return self.db.execute_and_fetchone(
            sql=sql, model_class=Snapshot, params=params
        )

    def create(
        self,
        ref: str,
        ref_sha: str,
        action: ProcessAction,
        state: ProcessState,
        current_datetime: datetime.datetime,
        user: str,
        pid: int,
    ) -> Snapshot:
        """Optimistically try and create a snapshot."""
        sql = """
        INSERT INTO snapshot (
            ref,
            ref_sha,
            action,
            state,
            created_at,
            created_by,
            pid,
            efd
        )
        SELECT
            :ref,
            :ref_sha,
            :action,
            :state,
            :created_at,
            :created_by,
            :pid,
            :efd
        WHERE NOT EXISTS (
            SELECT *
            FROM snapshot
            WHERE ref_sha = :ref_sha
        )
        RETURNING *
        """
        params = {
            "ref": ref,
            "ref_sha": ref_sha,
            "action": action,
            "state": state,
            "created_at": current_datetime,
            "created_by": user,
            "pid": pid,
            "efd": current_datetime,
        }
        return self.db.execute_and_fetchone(
            sql=sql, model_class=Snapshot, params=params
        )


CacheObject = t.NamedTuple(
    "Object",
    fields=[
        ("key", str),
        ("value", str),
    ],
)


class Cache:
    def __init__(self, db: Db) -> None:
        self.db = db

    def get(self, key: str) -> CacheObject | None:
        sql = "SELECT * FROM cache WHERE key = :key"
        params = {"key": key}
        return self.db.execute_and_fetchone(
            sql=sql, model_class=CacheObject, params=params
        )

    def set(self, key: str, value: str) -> CacheObject:
        sql = """
        INSERT INTO cache
        VALUES (:key, :value)
        RETURNING *
        """
        params = {"key": key, "value": value}
        return self.db.execute_and_fetchone(
            sql=sql, model_class=CacheObject, params=params
        )


def remove_stale_builds(
    builds_path: pathlib.Path,
    exclude_paths: list[pathlib.Path],
    keep_builds: int,
    log_title: str = "",
) -> None:
    """Remove stale builds."""
    existing_builds_path = [
        path for path in builds_path.glob("*") if path not in exclude_paths
    ]
    existing_builds_path_sorted = sort_paths_by_last_status_change(
        existing_builds_path, reverse=True
    )

    log_body = ""
    for path in existing_builds_path_sorted[keep_builds:]:
        shutil.rmtree(path)
        log_body += f"+ {path}"

    if log_body:
        clean_and_log(title=log_title, body=log_body)


def download_file(url: str, path: pathlib.Path) -> str:
    """Download the provided resource and calculate it's md5 hash."""
    hasher = hashlib.md5()
    resp = urllib.request.urlopen(url)

    with path.open("wb") as stream:
        while True:
            chunk = resp.read(1024)

            if not chunk:
                break

            stream.write(chunk)
            hasher.update(chunk)

    return hasher.hexdigest()


def download_tailwindcss_standalone(version: str, path: pathlib.Path) -> str:
    """Download the provided resource and calculate it's md5 hash."""
    url = f"https://github.com/tailwindlabs/tailwindcss/releases/download/{version}/tailwindcss-linux-x64"
    md5 = download_file(url, path)
    chmod_executable(path=path)
    return md5


def create_or_update_symlink(
    path: pathlib.Path, target: pathlib.Path, target_is_directory: bool
) -> None:
    """Create or update an existing symlink."""

    path.unlink(missing_ok=True)
    path.symlink_to(target=target, target_is_directory=target_is_directory)

    clean_and_log(title="Symlink created", body=f"+ {path} -> {target}")


def chmod_executable(path: pathlib.Path) -> None:
    """Make the provided file executable."""

    current_perms = path.stat().st_mode
    path.chmod(current_perms | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@contextlib.contextmanager
def acquire_advisory_lock(
    lock_path: pathlib.Path, non_blocking: bool = False
) -> t.Generator[bool, None, None]:
    """Acquire an advisory lock."""

    if non_blocking:
        flags = fcntl.LOCK_EX | fcntl.LOCK_NB
    else:
        flags = fcntl.LOCK_EX

    f = lock_path.open("w")
    fd = f.fileno()

    try:
        logger.debug("Acquiring advisory lock")
        fcntl.lockf(fd, flags)
        logger.debug("Advisory lock acquired")

        yield True
    except BlockingIOError:
        yield False
    finally:
        logger.debug("Releasing advisory lock")
        fcntl.lockf(fd, fcntl.LOCK_UN)
        f.close()
        logger.debug("Advisory lock released")


def is_pid_alive(pid: int) -> bool:
    """Returns True if a pid is alive."""

    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError as e:
        if e.errno == errno.ESRCH:
            return False
        elif e.errno == errno.EPERM:
            return True
        else:
            raise
    else:
        return True


def clean_and_log(title: str, body: str, level: str = "INFO") -> None:
    """Clean the provided log and logger info it."""

    if body:
        body_indented = textwrap.indent(body.strip(), "  ")
        content = f"{title}\n{body_indented}"
    else:
        content = f"{title}"

    content_clean = content.rstrip()

    if level == "DEBUG":
        logger.debug(content_clean)
    elif level == "ERROR":
        logger.error(content_clean)
    elif level == "WARN":
        logger.warning(content_clean)
    else:
        logger.info(content_clean)


@contextlib.contextmanager
def time_and_log(message_prefix: str) -> t.Generator[None, None, None]:
    """Provides a context manager for timing.

    The process time in milliseconds is automatically logged.
    """
    start_time = time.monotonic()
    yield
    total_seconds = time.monotonic() - start_time
    total_milliseconds = total_seconds * 1000
    logger.info(f"{message_prefix}{total_milliseconds:.0f}ms")


def load_module_from_path(path: pathlib.Path) -> ModuleType:
    """Load a python module from a path."""

    name = path.stem
    spec = importlib.util.spec_from_file_location(name, path)

    if not spec:
        raise BuildError(f"ERROR: Failed to load plugin specification {path!r}.")

    plugin_module = importlib.util.module_from_spec(spec)

    if spec.loader:
        spec.loader.exec_module(plugin_module)
    else:
        raise BuildError(f"ERROR: Specification not available for {path!r}.")

    return plugin_module


def get_resolved_path_absolute(path: pathlib.Path | str) -> pathlib.Path:
    """Get an absolute path from a posix path or string.."""

    if isinstance(path, str):
        path = pathlib.Path(path)

    if path.is_absolute():
        return path

    return path.expanduser().resolve().absolute()


def create_or_replace_symlink(
    path: pathlib.Path, target: pathlib.Path, target_is_directory: bool
) -> None:
    """Create a symlink or replace it if it already exists."""
    path.unlink(missing_ok=True)
    path.symlink_to(target, target_is_directory=target_is_directory)


def sort_paths_by_last_status_change(
    paths: list[pathlib.Path],
    reverse: bool,
) -> list[pathlib.Path]:
    return sorted(paths, key=lambda p: p.stat().st_ctime, reverse=reverse)


def get_environ_from_dotenv(path: pathlib.Path) -> dict[str, str]:
    """Extract env variables from a dotenv file."""
    environ = {}
    for line_raw in path.read_text().splitlines():
        line = line_raw.strip()

        if not line:
            continue

        if line.startswith("#"):
            continue

        expression = line.removeprefix("export ")
        name, value = expression.split("=", maxsplit=1)

        if value.startswith('"') and value.endswith('"'):
            value = value.removeprefix('"')
            value = value.removesuffix('"')

        environ[name] = value

    return environ


def render_template_and_save(
    path: pathlib.Path, template_name: str, jinja_env: Environment, **kwargs: t.Any
) -> str:
    env_template = jinja_env.get_template(template_name)
    env_content = env_template.render(**kwargs)
    path.write_text(env_content)
    hasher = hashlib.md5(env_content.encode())
    md5 = hasher.hexdigest()
    clean_and_log(
        title=f"Rendering template '{template_name}'", body=f"+ checksum={md5}"
    )
    return md5


def which_or_raise(executable: str) -> pathlib.Path:
    """Get the path to the executable.

    If it does not exist raise an error.
    """

    path_raw = shutil.which(executable)

    if not path_raw:
        raise DependencyError(f"{executable} not found in path.")

    return pathlib.Path(path_raw)


GIT_PATH = which_or_raise("git")


def get_python_path_from_venv(venv_path: pathlib.Path) -> pathlib.Path:
    """Return the path to the python binary within a virtual env."""

    return venv_path / "bin/python"


class SubProcess:
    """Utility for running sub processes."""

    def __init__(
        self,
        description: str,
        command: list[str],
        environ: dict[str, str],
        cwd: pathlib.Path | None,
        error_prefix: str = "",
    ) -> None:
        self.description = description
        self.command = command
        self.environ = environ
        self.cwd = cwd
        self.error_prefix = error_prefix

    def run(self) -> str:
        """Run the subprocess and log any exceptions."""

        cwd = self.cwd
        command = self.command
        environ = self.environ
        result = None

        try:
            result = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                env=environ,
                cwd=cwd,
            )

            # stderr is generally used logging info, but not always
            if result.stderr:
                output = result.stderr
            else:
                output = result.stdout

            title = self.description
            clean_and_log(title=title, body=output)

            return result.stdout
        except subprocess.SubprocessError as ex:
            error = getattr(ex, "stderr", "")

            if not error:
                error = f"{ex}"

            raise BuildError(f"{self.error_prefix}{error}") from ex


def systemctl_restart(
    service: str,
    sudo: bool,
    flags: str = "",
) -> None:
    """Restart the provided systemd service."""
    cwd = None
    environ = {**os.environ}

    command_raw = f"systemctl {flags} restart {service}"

    if sudo:
        command_raw = f"sudo {command_raw}"

    command = shlex.split(command_raw)

    process = SubProcess(
        description="Restart systemd service",
        command=command,
        environ=environ,
        cwd=cwd,
        error_prefix="Failed to restart systemd unit ",
    )
    process.run()


def systemctl_daemon_reload(
    sudo: bool,
    flags: str = "",
) -> None:
    """Restart the provided systemd service."""
    cwd = None
    environ = {**os.environ}

    command_raw = f"systemctl {flags} daemon-reload"

    if sudo:
        command_raw = f"sudo {command_raw}"

    command = shlex.split(command_raw)

    process = SubProcess(
        description="Reload systemd units",
        command=command,
        environ=environ,
        cwd=cwd,
        error_prefix="Failed to reload systemd units ",
    )
    process.run()


def systemctl_enable(
    service: str,
    sudo: bool,
    flags: str = "",
) -> None:
    """Enable the provided systemd service."""
    cwd = None
    environ = {**os.environ}

    command_raw = f"systemctl enable {flags} {service}"

    if sudo:
        command_raw = f"sudo {command_raw}"

    command = shlex.split(command_raw)

    process = SubProcess(
        description="Enable systemd service",
        command=command,
        environ=environ,
        cwd=cwd,
        error_prefix="Failed to enable systemd unit ",
    )
    process.run()


def git_reset_hard(
    ref_sha: str,
    build_path: pathlib.Path,
    environ: dict[str, str],
    git_path: pathlib.Path = GIT_PATH,
) -> None:
    """Helper for running git reset."""
    command = shlex.split(f"{git_path} -C {build_path} reset --hard {ref_sha}")
    process = SubProcess(
        description="Running git reset",
        command=command,
        environ=environ,
        cwd=None,
        error_prefix="Failed to git run reset ",
    )
    process.run()


def git_fetch(
    build_path: pathlib.Path,
    environ: dict[str, str],
    git_path: pathlib.Path = GIT_PATH,
) -> None:
    """Helper for running git fetch."""
    command = shlex.split(f"{git_path} -C {build_path} fetch")
    process = SubProcess(
        description="Running git fetch",
        command=command,
        environ=environ,
        cwd=None,
        error_prefix="Failed to git run fetch ",
    )
    process.run()


def git_clone(
    repo_path: pathlib.Path,
    build_path: pathlib.Path,
    environ: dict[str, str],
    git_path: pathlib.Path = GIT_PATH,
) -> None:
    """Helper for running git clone."""
    command = shlex.split(f"{git_path} -C {repo_path} clone {repo_path} {build_path}")
    process = SubProcess(
        description="Running git clone",
        command=command,
        environ=environ,
        cwd=None,
        error_prefix="Failed to git run clone ",
    )
    process.run()


def git_checkout_ref_sha(
    ref_sha: str,
    repo_path: pathlib.Path,
    build_path: pathlib.Path,
    environ: dict[str, str],
    git_path: pathlib.Path = GIT_PATH,
) -> None:
    """This procedure runs the following process:

    1. Clone the repository if it does not exist
    2. Fetch and reset to the ref sha if the repository does exist
    """
    if build_path.exists():
        git_fetch(
            build_path=build_path,
            git_path=git_path,
            environ=environ,
        )
        git_reset_hard(
            ref_sha=ref_sha,
            build_path=build_path,
            git_path=git_path,
            environ=environ,
        )
    else:
        git_clone(
            repo_path=repo_path,
            build_path=build_path,
            git_path=git_path,
            environ=environ,
        )
        git_reset_hard(
            ref_sha=ref_sha,
            build_path=build_path,
            git_path=git_path,
            environ=environ,
        )


def create_venv_with_uv(
    build_path: pathlib.Path,
    venv_path: pathlib.Path,
    venv_prompt: str,
    uv_path: pathlib.Path,
    environ: dict[str, str],
) -> None:
    """Create a virtual env using uv."""
    environ |= {"VIRTUAL_ENV": f"{venv_path}", "UV_VENV_CLEAR": "1"}

    cwd = build_path
    command = shlex.split(
        f"{uv_path} venv --no-project --prompt {venv_prompt} {venv_path}"
    )

    process = SubProcess(
        description="Creating virtual env with uv",
        command=command,
        environ=environ,
        cwd=cwd,
        error_prefix="Failed to create venv ",
    )
    process.run()


def get_ref_sha(
    repo_path: pathlib.Path,
    ref: str,
    environ: dict[str, str],
    git_path: pathlib.Path = GIT_PATH,
) -> str:
    """Get a git sha from a ref."""
    cwd = None
    command = shlex.split(f"{git_path} -C {repo_path} rev-parse --short {ref}")
    process = SubProcess(
        description=f"Fetching git sha for {ref}",
        command=command,
        environ=environ,
        cwd=cwd,
        error_prefix="Failed to get ref sha for {ref!r} ",
    )
    result = process.run()
    return result.strip()


def get_is_bare_repo(
    repo_path: pathlib.Path,
    environ: dict[str, str],
    git_path: pathlib.Path = GIT_PATH,
) -> bool:
    """Return True if the repo is a bare repo."""
    command = shlex.split(f"{git_path} -C {repo_path} rev-parse --is-bare-repository")
    process = SubProcess(
        description="Checking if git repo is bare",
        command=command,
        environ=environ,
        cwd=None,
        error_prefix="Failed to check if repository is a bare repository ",
    )
    result = process.run()
    return result.strip() == "true"


def sync_deps_with_uv(
    build_path: pathlib.Path,
    venv_path: pathlib.Path,
    uv_path: pathlib.Path,
    environ: dict[str, str],
) -> None:
    """Sync dependencies with uv."""
    cwd = build_path
    environ |= {"VIRTUAL_ENV": f"{venv_path}", "UV_VENV_CLEAR": "1"}
    command = shlex.split(f"{uv_path} sync")

    process = SubProcess(
        description="Syncing dependencies with uv",
        command=command,
        environ=environ,
        cwd=cwd,
        error_prefix="Failed to sync dependencies ",
    )
    process.run()


def pip_install_with_uv(
    build_path: pathlib.Path,
    venv_path: pathlib.Path,
    requirements_list: list[str],
    uv_path: pathlib.Path,
    environ: dict[str, str],
) -> None:
    """Pip install dependencies with uv."""
    cwd = build_path
    environ |= {"VIRTUAL_ENV": f"{venv_path}", "UV_VENV_CLEAR": "1"}

    requirements = " ".join(requirements_list)
    command = shlex.split(f"{uv_path} pip install {requirements}")

    process = SubProcess(
        description="Pip installing dependencies with uv",
        command=command,
        environ=environ,
        cwd=cwd,
        error_prefix="Failed to pip install requirements ",
    )
    process.run()


def tailwindcss_build_and_minify(
    build_path: pathlib.Path,
    input_file: pathlib.Path,
    output_file: pathlib.Path,
    tailwindcss_path: pathlib.Path,
    environ: dict[str, str],
) -> None:
    """Run the tailwindcss build and minify command."""
    cwd = build_path
    command = shlex.split(
        f"{tailwindcss_path} --input={input_file} --output={output_file} --minify"
    )
    process = SubProcess(
        description="Compiling and minifying tailwind css",
        command=command,
        environ=environ,
        cwd=cwd,
        error_prefix="Failed to build and minify tailwind styles ",
    )
    process.run()


def django_migrate(
    build_path: pathlib.Path,
    manage_path: pathlib.Path,
    venv_path: pathlib.Path,
    environ: dict[str, str],
) -> None:
    """Run the django migrate command."""
    cwd = build_path
    environ |= {"VIRTUAL_ENV": f"{venv_path}"}
    python_path = get_python_path_from_venv(venv_path)

    command = shlex.split(f"{python_path} {manage_path} migrate")
    process = SubProcess(
        description="Running django migrations",
        command=command,
        environ=environ,
        cwd=cwd,
        error_prefix="Failed to run django migrations ",
    )
    process.run()


def django_collectstatic(
    build_path: pathlib.Path,
    manage_path: pathlib.Path,
    venv_path: pathlib.Path,
    environ: dict[str, str],
) -> None:
    """Run the django check command."""
    cwd = build_path
    environ |= {"VIRTUAL_ENV": f"{venv_path}"}
    python_path = get_python_path_from_venv(venv_path)

    command = shlex.split(f"{python_path} {manage_path} collectstatic --no-input")
    process = SubProcess(
        description="Running django collectstatic",
        command=command,
        environ=environ,
        cwd=cwd,
        error_prefix="Failed to run django collectstatic ",
    )
    process.run()


def django_check(
    build_path: pathlib.Path,
    manage_path: pathlib.Path,
    venv_path: pathlib.Path,
    environ: dict[str, str],
) -> None:
    """Run the django check command."""
    cwd = build_path
    environ |= {"VIRTUAL_ENV": f"{venv_path}"}
    python_path = get_python_path_from_venv(venv_path)

    command = shlex.split(f"{python_path} {manage_path} check")
    process = SubProcess(
        description="Running django check",
        command=command,
        environ=environ,
        cwd=cwd,
        error_prefix="Failed to run django check ",
    )
    process.run()
