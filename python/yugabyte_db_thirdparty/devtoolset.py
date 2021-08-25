# Copyright (c) Yugabyte, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not use this file except
# in compliance with the License. You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software distributed under the License
# is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express
# or implied. See the License for the specific language governing permissions and limitations
# under the License.
#

"""
Support for RedHat devtoolsets, also known as gcc-toolsets.
"""

import os
import subprocess
import shlex

from typing import Set, List

from yugabyte_db_thirdparty.util import log, fatal
from yugabyte_db_thirdparty.string_util import split_into_word_set


DEVTOOLSET_ENV_VARS: Set[str] = split_into_word_set("""
    INFOPATH
    LD_LIBRARY_PATH
    MANPATH
    PATH
    PCP_DIR
    PKG_CONFIG_PATH
    PYTHONPATH
""")

DEVTOOLSET_ENV_VARS_OK_IF_UNSET: Set[str] = set(['PERL5LIB'])

DEVTOOLSET_DIR_NAMES = ['devtoolset', 'gcc-toolset']


def activate_devtoolset(devtoolset_number: int) -> None:
    devtoolset_enable_script_candidates = [
        f'/opt/rh/{toolset_name_prefix}-{devtoolset_number}/enable'
        for toolset_name_prefix in DEVTOOLSET_DIR_NAMES
    ]
    existing_devtoolset_enable_scripts = [
        script_path for script_path in devtoolset_enable_script_candidates
        if os.path.exists(script_path)
    ]
    if len(existing_devtoolset_enable_scripts) != 1:
        fatal(
            f"Expected exactly one of the scripts to exist: {devtoolset_enable_script_candidates}. "
            f"Found that {len(existing_devtoolset_enable_scripts)} exist.")
    devtoolset_enable_script = existing_devtoolset_enable_scripts[0]

    log("Enabling devtoolset-%s by sourcing the script %s",
        devtoolset_number, devtoolset_enable_script)
    if not os.path.exists(devtoolset_enable_script):
        raise IOError("Devtoolset script does not exist: %s" % devtoolset_enable_script)

    echo_env_vars_str = '; '.join(
        ['echo %s=$%s' % (k, shlex.quote(k)) for k in DEVTOOLSET_ENV_VARS])
    cmd_args = ['bash', '-c', '. "%s" && ( %s )' % (devtoolset_enable_script, echo_env_vars_str)]
    log("Running command: %s", cmd_args)
    devtoolset_env_str = subprocess.check_output(cmd_args).decode('utf-8')

    found_vars = set()
    for line in devtoolset_env_str.split("\n"):
        line = line.strip()
        if not line:
            continue
        k, v = line.split("=", 1)
        if k in DEVTOOLSET_ENV_VARS:
            log("Setting %s to: %s", k, v)
            os.environ[k] = v
            found_vars.add(k)
    missing_vars = set()
    for var_name in DEVTOOLSET_ENV_VARS:
        if var_name not in found_vars:
            log("Did not set env var %s for devtoolset-%d", var_name, devtoolset_number)
            if var_name not in DEVTOOLSET_ENV_VARS_OK_IF_UNSET:
                missing_vars.add(var_name)
    if missing_vars:
        raise IOError(
            "Invalid environment after running devtoolset script %s. Did not set vars: %s" % (
                devtoolset_enable_script, ', '.join(sorted(missing_vars))
            ))


def validate_devtoolset_compiler_path(compiler_path: str, devtoolset: int) -> None:
    substring_found = False
    devtoolset_substrings: List[str] = []
    for substring_candidate in DEVTOOLSET_DIR_NAMES:
        devtoolset_substring = f'/{substring_candidate}-{devtoolset}/'
        devtoolset_substrings.append(devtoolset_substring)
        if devtoolset_substring in compiler_path:
            substring_found = True
            break

    if not substring_found:
        raise ValueError(
            f"Invalid compiler path: {compiler_path}. No devtoolset-related substring "
            f"found: {devtoolset_substrings}")
