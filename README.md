# Bou

Bou is my application build tool.

It makes use of a plugin architecture using [pluggy](https://github.com/pytest-dev/pluggy).

In summary bou provides a CLI tool which hooks into your build implementations. It provides helpers for some routine tasks within the `contrib` module.

It was created specifically for django applications but can be used more generally.

## Usage

A build process consists of the following steps:

```text
- configure
- pre_build
- build
- post_build
```

A release process consists of the following steps:

```text
- configure
- pre_release
- release
- post_release
```

You are required to implement these functions (At least the configure, build and release).

The `bou` CLI provides a mechanism to initialize a new build.

```console
python -m bou init build.py
```

This will generate an empty build system.

```python
import logging
import pathlib
from jinja2 import Environment, FileSystemLoader
from bou.fpi import hookimpl_v1, Config
from bou.contrib import Cache
logger = logging.getLogger('bou')

@hookimpl_v1
def configure(ref: str, ref_sha: str, builds_path: pathlib.Path, repo_path: pathlib.Path, jinja_env: Environment, cache: Cache) -> Config:
    build_path = pathlib.Path('/tmp/bou/builds')
    jinja_env.loader = FileSystemLoader(build_path)
    return Config(build_path=build_path)

@hookimpl_v1
def pre_build(ref: str, ref_sha: str, builds_path: pathlib.Path, build_path: pathlib.Path, repo_path: pathlib.Path, jinja_env: Environment, config: Config, cache: Cache) -> None:
    ...

@hookimpl_v1
def build(ref: str, ref_sha: str, builds_path: pathlib.Path, build_path: pathlib.Path, repo_path: pathlib.Path, jinja_env: Environment, config: Config, cache: Cache) -> None:
    ...

@hookimpl_v1
def post_build(ref: str, ref_sha: str, builds_path: pathlib.Path, build_path: pathlib.Path, repo_path: pathlib.Path, jinja_env: Environment, config: Config, cache: Cache) -> None:
    ...

@hookimpl_v1
def pre_release(ref: str, ref_sha: str, builds_path: pathlib.Path, build_path: pathlib.Path, repo_path: pathlib.Path, jinja_env: Environment, config: Config, cache: Cache) -> None:
    ...

@hookimpl_v1
def release(ref: str, ref_sha: str, builds_path: pathlib.Path, build_path: pathlib.Path, repo_path: pathlib.Path, jinja_env: Environment, config: Config, cache: Cache) -> None:
    ...

@hookimpl_v1
def post_release(ref: str, ref_sha: str, builds_path: pathlib.Path, build_path: pathlib.Path, repo_path: pathlib.Path, jinja_env: Environment, config: Config, cache: Cache) -> None:
    ...
```

## Building

Once you have implemented your build system you can run the build process as follows:

```console
> python -m bou build -B example/build.py -b example/builds -d example/bou.db -r example/repo  main $USER

INFO:bou.contrib:Fetching git sha for main
  b3fe53c
INFO:bou.contrib:Checking if git repo is bare
  true
INFO:bou:Optimistic lock acquisition race won for snapshot 'b3fe53c', build will begin shortly
INFO:bou.contrib:Configuration execution time 0ms
INFO:bou.contrib:Pre-build execution time 0ms
INFO:bou:Creating build main b3fe53c
INFO:bou.contrib:Running git fetch
INFO:bou.contrib:Running git reset
  HEAD is now at b3fe53c feat: Initial commit
INFO:bou.contrib:Creating virtual env with uv
  Using CPython 3.13.6
  Creating virtual environment at: .venv
  Activate with: source .venv/bin/activate
INFO:bou.contrib:Pip installing dependencies with uv
  Resolved 5 packages in 3ms
  Installed 5 packages in 73ms
   + asgiref==3.9.2
   + click==8.3.0
   + django==5.2.7
   + granian==2.5.4
   + sqlparse==0.5.3
INFO:bou.contrib:Rendering template 'example/services/env'
  + checksum=a7555edce7cdf4b3a9d17cff4641bcac
INFO:bou.contrib:Rendering template 'example/services/app.service'
  + checksum=0f97ce31c642e81261526d58c14fdbeb
INFO:bou.contrib:Compiling and minifying tailwind css
  ≈ tailwindcss v4.1.13

  Done in 26ms
INFO:bou.contrib:Running django collectstatic
  0 static files copied to '/home/bradleyk/clients/bou/example/builds/b3fe53c/example/staticfiles', 129 unmodified.
INFO:bou.contrib:Build execution time 720ms
INFO:bou.contrib:Post-build execution time 0ms
```

See the example directory for a more detailed example.

## Releasing

Once you have implemented your build system you can run the build process as follows:

```console
> python -m bou release -B example/build.py -b example/builds -d example/bou.db -r example/repo  main $USER

INFO:bou.contrib:Fetching git sha for main
  b3fe53c
INFO:bou.contrib:Checking if git repo is bare
  true
INFO:bou:Optimistic lock acquisition race won for snapshot 'b3fe53c', build will begin shortly
INFO:bou.contrib:Configuration execution time 0ms
INFO:bou.contrib:Pre-release execution time 0ms
INFO:bou.contrib:Running django check
  System check identified no issues (0 silenced).
INFO:bou.contrib:Running django migrations
  Operations to perform:
    Apply all migrations: admin, auth, contenttypes, sessions
  Running migrations:
    No migrations to apply.
INFO:bou.contrib:Symlink created
  + /home/bradleyk/clients/bou/example/builds/latest -> /home/bradleyk/clients/bou/example/builds/b3fe53c
INFO:bou.contrib:Release execution time 326ms
INFO:bou.contrib:Post-release execution time 0ms
INFO:bou.contrib:Total execution time 340ms
```

See the example directory for a more detailed example.