"""Foreign plugin interface."""

import collections
import logging
import pathlib
import typing as t

from jinja2 import Environment
import pluggy

from bou.errors import ConfigError
from bou.contrib import Cache

logger = logging.getLogger("bou")


pm = pluggy.PluginManager("bou")
hookspec_v1 = pluggy.HookspecMarker("bou")
hookimpl_v1 = pluggy.HookimplMarker("bou")


class Config(collections.UserDict):
    """Models the system config."""

    def __init__(
        self,
        build_path: pathlib.Path,
        **kwargs: t.Any,
    ) -> None:
        super().__init__(**kwargs)
        self.data["build_path"] = build_path


class BuildSpec:
    @hookspec_v1(firstresult=True)
    def configure(
        self,
        ref: str,
        ref_sha: str,
        builds_path: pathlib.Path,
        repo_path: pathlib.Path,
        jinja_env: Environment,
        cache: Cache,
    ) -> Config: ...

    @hookspec_v1
    def pre_build(
        self,
        ref: str,
        ref_sha: str,
        repo_path: pathlib.Path,
        builds_path: pathlib.Path,
        build_path: pathlib.Path,
        jinja_env: Environment,
        config: Config,
        cache: Cache,
    ) -> None: ...

    @hookspec_v1
    def build(
        self,
        ref: str,
        ref_sha: str,
        repo_path: pathlib.Path,
        builds_path: pathlib.Path,
        build_path: pathlib.Path,
        jinja_env: Environment,
        config: Config,
        cache: Cache,
    ) -> None: ...

    @hookspec_v1
    def post_build(
        self,
        ref: str,
        ref_sha: str,
        repo_path: pathlib.Path,
        builds_path: pathlib.Path,
        build_path: pathlib.Path,
        jinja_env: Environment,
        config: Config,
        cache: Cache,
    ) -> None: ...

    @hookspec_v1
    def pre_release(
        self,
        ref: str,
        ref_sha: str,
        repo_path: pathlib.Path,
        builds_path: pathlib.Path,
        build_path: pathlib.Path,
        jinja_env: Environment,
        config: Config,
        cache: Cache,
    ) -> None: ...

    @hookspec_v1
    def release(
        self,
        ref: str,
        ref_sha: str,
        repo_path: pathlib.Path,
        builds_path: pathlib.Path,
        build_path: pathlib.Path,
        jinja_env: Environment,
        config: Config,
        cache: Cache,
    ) -> None: ...

    @hookspec_v1
    def post_release(
        self,
        ref: str,
        ref_sha: str,
        repo_path: pathlib.Path,
        builds_path: pathlib.Path,
        build_path: pathlib.Path,
        jinja_env: Environment,
        config: Config,
        cache: Cache,
    ) -> None: ...


class BuildPlugin:
    """Base hook implementation.

    This plugin is name spaced with a class, your external build system
    should expose the methods on this class at a module level.
    """

    @hookimpl_v1(wrapper=True)
    def configure(
        self,
        ref: str,
        ref_sha: str,
        builds_path: pathlib.Path,
        repo_path: pathlib.Path,
        jinja_env: Environment,
        cache: Cache,
    ) -> t.Generator[None, Config, Config]:
        """Get the config from the build system."""
        config: Config = yield

        # Validation
        if "build_path" not in config:
            raise ConfigError("build_path is a required key")

        build_path = config["build_path"]

        if not isinstance(build_path, pathlib.Path):
            raise ConfigError("build_path should be of type pathlib.Path")

        return config

    @hookimpl_v1(tryfirst=True)
    def pre_build(
        self,
        ref: str,
        ref_sha: str,
        builds_path: pathlib.Path,
        build_path: pathlib.Path,
        repo_path: pathlib.Path,
        jinja_env: Environment,
        config: Config,
        cache: Cache,
    ) -> None:
        """Run validation/preparation before the build starts."""
        logger.debug("(base hook pre_build)")

    @hookimpl_v1(tryfirst=True)
    def build(
        self,
        ref: str,
        ref_sha: str,
        builds_path: pathlib.Path,
        build_path: pathlib.Path,
        repo_path: pathlib.Path,
        jinja_env: Environment,
        config: Config,
        cache: Cache,
    ) -> None:
        """Create an isolated build."""
        logger.debug("(base hook build)")

    @hookimpl_v1(tryfirst=True)
    def post_build(
        self,
        ref: str,
        ref_sha: str,
        builds_path: pathlib.Path,
        build_path: pathlib.Path,
        repo_path: pathlib.Path,
        jinja_env: Environment,
        config: Config,
        cache: Cache,
    ) -> None:
        """Run validation/preparation post the build completion."""
        logger.debug("(base hook post_build)")

    @hookimpl_v1(tryfirst=True)
    def pre_release(
        self,
        ref: str,
        ref_sha: str,
        builds_path: pathlib.Path,
        build_path: pathlib.Path,
        repo_path: pathlib.Path,
        jinja_env: Environment,
        config: Config,
        cache: Cache,
    ) -> None:
        """Run validation/preparation before the build starts."""
        logger.debug("(base hook pre_build)")

    @hookimpl_v1(tryfirst=True)
    def release(
        self,
        ref: str,
        ref_sha: str,
        builds_path: pathlib.Path,
        build_path: pathlib.Path,
        repo_path: pathlib.Path,
        jinja_env: Environment,
        config: Config,
        cache: Cache,
    ) -> None:
        """Create an isolated build."""
        logger.debug("(base hook build)")

    @hookimpl_v1(tryfirst=True)
    def post_release(
        self,
        ref: str,
        ref_sha: str,
        builds_path: pathlib.Path,
        build_path: pathlib.Path,
        repo_path: pathlib.Path,
        jinja_env: Environment,
        config: Config,
        cache: Cache,
    ) -> None:
        """Run validation/preparation post the build completion."""
        logger.debug("(base hook post_build)")
