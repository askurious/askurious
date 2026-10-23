# modules

Add-on modules (app/plugins.py) go up here as wheels with a deploy: `deploy/docker/deploy-docker.sh` builds
them from the checkouts named in `MODULES_SRC` and the Dockerfile installs every `*.whl` of this folder. In the
code the folder holds only this README.
