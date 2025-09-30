"""Example build system for a django application."""

import logging
import os
import pathlib

from jinja2 import Environment, FileSystemLoader

from bou.contrib import (
    Cache,
    create_or_update_symlink,
    create_venv_with_uv,
    django_check,
    django_collectstatic,
    django_migrate,
    download_tailwindcss_standalone,
    git_checkout_ref_sha,
    pip_install_with_uv,
    remove_stale_builds,
    render_template_and_save,
    tailwindcss_build_and_minify,
    which_or_raise,
)
from bou.fpi import Config, hookimpl_v1

logger = logging.getLogger("bou")


uv_path = which_or_raise("uv")


@hookimpl_v1
def configure(
    ref: str,
    ref_sha: str,
    builds_path: pathlib.Path,
    repo_path: pathlib.Path,
    jinja_env: Environment,
    cache: Cache,
) -> Config:
    """Configure the build system."""

    build_path = builds_path / ref_sha
    latest_build_path = builds_path / "latest"
    manage_path = build_path / "example/manage.py"
    venv_path = build_path / ".venv"

    root_path = pathlib.Path(__file__).parent
    tailwindcss_binaries_path = root_path / "tailwindcss"
    tailwindcss_binaries_path.mkdir(exist_ok=True)
    tailwindcss_version = "v4.1.13"
    tailwindcss_path = tailwindcss_binaries_path / tailwindcss_version

    # Configure jinja2
    jinja_env.loader = FileSystemLoader(build_path)

    return Config(
        build_path=build_path,
        tailwindcss_binaries_path=tailwindcss_binaries_path,
        tailwindcss_path=tailwindcss_path,
        tailwindcss_version=tailwindcss_version,
        manage_path=manage_path,
        venv_path=venv_path,
        latest_build_path=latest_build_path,
    )


@hookimpl_v1
def build(
    ref: str,
    ref_sha: str,
    builds_path: pathlib.Path,
    build_path: pathlib.Path,
    repo_path: pathlib.Path,
    jinja_env: Environment,
    config: Config,
    cache: Cache,
) -> None:
    """Django application build process."""

    logger.info(f"Creating build {ref} {ref_sha}")

    venv_prompt = ref_sha
    requirements_path = build_path / "example/requirements.prd.txt"
    tailwindcss_input_file_path = build_path / "example/static/css/_tailwind.css"
    tailwindcss_output_file_path = build_path / "example/static/css/tailwind.css"
    static_root_path = build_path / "staticfiles"
    tailwindcss_path = config["tailwindcss_path"]
    app_service_path = (
        build_path / "app.service"
    )  # Invalid systemd unit path for demonstration
    app_env_path = build_path / "env"
    tailwindcss_version = config["tailwindcss_version"]
    manage_path = config["manage_path"]
    venv_path = config["venv_path"]
    granian_path = venv_path / "bin/granian"
    environ = {**os.environ}

    # Download tailwindcss if it does not exist
    if not tailwindcss_path.exists():
        logger.info(f"Downloading tailwindcss '{tailwindcss_version}' standalone")

        download_tailwindcss_standalone(
            version=tailwindcss_version, path=tailwindcss_path
        )

    # Run the process
    git_checkout_ref_sha(
        ref_sha=ref_sha,
        repo_path=repo_path,
        build_path=build_path,
        environ=environ,
    )
    create_venv_with_uv(
        build_path=build_path,
        venv_path=venv_path,
        venv_prompt=venv_prompt,
        uv_path=uv_path,
        environ=environ,
    )
    pip_install_with_uv(
        build_path=build_path,
        venv_path=venv_path,
        requirements_list=[f"-r {requirements_path}"],
        uv_path=uv_path,
        environ=environ,
    )
    render_template_and_save(
        path=app_env_path,
        template_name="example/services/env",
        jinja_env=jinja_env,
        static_root_path=static_root_path,
        tailwindcss_version=tailwindcss_version,
        environ=environ,
    )
    render_template_and_save(
        path=app_service_path,
        template_name="example/services/app.service",
        jinja_env=jinja_env,
        granian_path=granian_path,
        working_dir_path=build_path,
        environ=environ,
    )
    tailwindcss_build_and_minify(
        build_path=build_path,
        input_file=tailwindcss_input_file_path,
        output_file=tailwindcss_output_file_path,
        tailwindcss_path=tailwindcss_path,
        environ=environ,
    )
    django_collectstatic(
        build_path=build_path,
        manage_path=manage_path,
        venv_path=venv_path,
        environ=environ,
    )


@hookimpl_v1
def release(
    ref: str,
    ref_sha: str,
    builds_path: pathlib.Path,
    build_path: pathlib.Path,
    repo_path: pathlib.Path,
    jinja_env: Environment,
    config: Config,
) -> None:
    """Django application release process."""

    venv_path = config["venv_path"]
    manage_path = config["manage_path"]
    latest_build_path = config["latest_build_path"]
    environ = {**os.environ}

    django_check(
        build_path=build_path,
        manage_path=manage_path,
        venv_path=venv_path,
        environ=environ,
    )
    django_migrate(
        build_path=build_path,
        manage_path=manage_path,
        venv_path=venv_path,
        environ=environ,
    )
    # user = os.getlogin()
    # flags = f"--user -M {user}@"
    # systemctl_daemon_reload(flags=flags, sudo=False)
    # systemctl_enable("test.service", flags=flags, sudo=False)
    create_or_update_symlink(
        path=latest_build_path, target=build_path, target_is_directory=True
    )
    # systemctl_restart("test.service", flags=flags, sudo=False)


@hookimpl_v1
def post_release(
    ref: str,
    ref_sha: str,
    builds_path: pathlib.Path,
    build_path: pathlib.Path,
    repo_path: pathlib.Path,
    jinja_env: Environment,
    config: Config,
) -> None:
    """Django application post release process."""

    latest_build_path = config["latest_build_path"]
    remove_stale_builds(
        builds_path=builds_path,
        exclude_paths=[latest_build_path],
        keep_builds=2,
        log_title="Removing stale builds",
    )
