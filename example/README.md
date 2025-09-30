## Django Application

Create a builds folder.

```console
> mkdir -p example/builds
```

Create a bare repo to store the bou source code.

```console
> git -C example/repo init --bare
```

Add the repo created above as a remote.

```console
> git remote add example ./example/repo
```

Install the post-receive git hook

```console
> python -m bou install -B example/build.py -b example/builds -d example/bou.db -r example/repo -c .venv/bin/bou 
```

Push to the repo.

```console
> git push example

Enumerating objects: 35, done.
Counting objects: 100% (35/35), done.
Delta compression using up to 16 threads
Compressing objects: 100% (29/29), done.
Writing objects: 100% (35/35), 23.21 KiB | 11.60 MiB/s, done.
Total 35 (delta 0), reused 0 (delta 0), pack-reused 0 (from 0)
remote: INFO:bou.contrib:Fetching git sha for refs/heads/main
remote:   1d62dc4
remote: INFO:bou.contrib:Checking if git repo is bare
remote:   true
remote: INFO:bou.contrib:Configuration execution time 0ms
remote: INFO:bou.contrib:Pre-build execution time 0ms
remote: INFO:bou:Creating build refs/heads/main 1d62dc4
remote: INFO:bou.contrib:Running git clone
remote:   Cloning into '/home/bradleyk/clients/bou/example/builds/1d62dc4'...
remote:   done.
remote: INFO:bou.contrib:Running git reset
remote:   HEAD is now at 1d62dc4 feat: Initial commit
remote: INFO:bou.contrib:Creating virtual env with uv
remote:   Using CPython 3.13.6
remote:   Creating virtual environment at: .venv
remote:   Activate with: source .venv/bin/activate
remote: INFO:bou.contrib:Pip installing dependencies with uv
remote:   Resolved 5 packages in 3ms
remote:   Installed 5 packages in 71ms
remote:    + asgiref==3.9.2
remote:    + click==8.3.0
remote:    + django==5.2.7
remote:    + granian==2.5.4
remote:    + sqlparse==0.5.3
remote: INFO:bou.contrib:Rendering template 'example/services/env'
remote:   + checksum=1d13832c1cd89d809dcdee04827462e7
remote: INFO:bou.contrib:Rendering template 'example/services/app.service'
remote:   + checksum=3a8aad5d71acff04c97ba68eae2b17e7
remote: INFO:bou.contrib:Compiling and minifying tailwind css
remote:   ≈ tailwindcss v4.1.13
remote: 
remote:   Done in 27ms
remote: INFO:bou.contrib:Running django collectstatic
remote:   129 static files copied to '/home/bradleyk/clients/bou/example/builds/1d62dc4/example/staticfiles'.
remote: INFO:bou.contrib:Build execution time 679ms
remote: INFO:bou.contrib:Post-build execution time 0ms
remote: INFO:bou.contrib:Fetching git sha for refs/heads/main
remote:   1d62dc4
remote: INFO:bou.contrib:Checking if git repo is bare
remote:   true
remote: INFO:bou:Optimistic lock acquisition race won for snapshot '1d62dc4', build will begin shortly
remote: INFO:bou.contrib:Configuration execution time 0ms
remote: INFO:bou.contrib:Pre-release execution time 0ms
remote: INFO:bou.contrib:Running django check
remote:   System check identified no issues (0 silenced).
remote: INFO:bou.contrib:Running django migrations
remote:   Operations to perform:
remote:     Apply all migrations: admin, auth, contenttypes, sessions
remote:   Running migrations:
remote:     Applying contenttypes.0001_initial... OK
remote:     Applying auth.0001_initial... OK
remote:     Applying admin.0001_initial... OK
remote:     Applying admin.0002_logentry_remove_auto_add... OK
remote:     Applying admin.0003_logentry_add_action_flag_choices... OK
remote:     Applying contenttypes.0002_remove_content_type_name... OK
remote:     Applying auth.0002_alter_permission_name_max_length... OK
remote:     Applying auth.0003_alter_user_email_max_length... OK
remote:     Applying auth.0004_alter_user_username_opts... OK
remote:     Applying auth.0005_alter_user_last_login_null... OK
remote:     Applying auth.0006_require_contenttypes_0002... OK
remote:     Applying auth.0007_alter_validators_add_error_messages... OK
remote:     Applying auth.0008_alter_user_username_max_length... OK
remote:     Applying auth.0009_alter_user_last_name_max_length... OK
remote:     Applying auth.0010_alter_group_name_max_length... OK
remote:     Applying auth.0011_update_proxy_permissions... OK
remote:     Applying auth.0012_alter_user_first_name_max_length... OK
remote:     Applying sessions.0001_initial... OK
remote: INFO:bou.contrib:Symlink created
remote:   + /home/bradleyk/clients/bou/example/builds/latest -> /home/bradleyk/clients/bou/example/builds/1d62dc4
remote: INFO:bou.contrib:Release execution time 643ms
remote: INFO:bou.contrib:Removing stale builds
remote:   + /home/bradleyk/clients/bou/example/builds/1d5bc11
remote: INFO:bou.contrib:Post-release execution time 87ms
remote: INFO:bou.contrib:Total execution time 736ms
```

Run the build manually.

```console
> python -m bou build -B example/build.py -b example/builds -d example/bou.db -r example/repo  main $USER
```

Run the release manually.

```console
> python -m bou release -B example/build.py -b example/builds -d example/bou.db -r example/repo  main $USER
```