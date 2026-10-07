#!/usr/bin/env bash
# Run the test suite inside Flatout's Docker image, where flatpak, ostree and
# gpg are installed, so the repository tests (tests/test_repo.py) run too
# instead of being skipped.
#
#   tools/test-in-docker.sh            # the whole suite
#   tools/test-in-docker.sh -k repo    # arguments go to pytest
set -euo pipefail
cd "$(dirname "$0")/.."
docker build -q -t flatout:test . >/dev/null
docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -v "$PWD:/src:ro" -w /tmp flatout:test sh -c '
  cp -r /src /tmp/src && cd /tmp/src && rm -rf .venv var
  pip install --quiet --user --no-warn-script-location pytest==8.3.3 >/dev/null
  python -m pytest -q -p no:cacheprovider "$@"
' sh "$@"
