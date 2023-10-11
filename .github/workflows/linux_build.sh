#!/usr/bin/env bash

set -euo pipefail

df -H .
checkout_dir=$PWD
export YB_SKIP_UPLOAD=${SKIP_UPLOAD:-}
echo "Building in directory: $checkout_dir"
docker run -t \
  --cap-add=SYS_PTRACE \
  -u root \
  -e GITHUB_TOKEN \
  -e SNYK_TOKEN \
  -e YB_BUILD_THIRDPARTY_ARGS \
  -e YB_THIRDPARTY_ARCHIVE_NAME_SUFFIX \
  -e YB_SKIP_UPLOAD \
  "--mount=type=bind,src=$checkout_dir,dst=/opt/yb-build/thirdparty/checkout" \
  "$YB_DOCKER_IMAGE" \
  bash -c '
    set -euo pipefail
    cd /opt/yb-build/thirdparty/checkout
    # To avoid the "unsafe repository owned by someone else" git error:
    chown -R "$(whoami)" "$PWD"
    ./build_and_release.sh
  '
