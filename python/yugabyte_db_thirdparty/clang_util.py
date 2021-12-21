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

import subprocess
import os
from typing import Optional, List
from yugabyte_db_thirdparty.string_util import shlex_join

LIBRARY_DIRS_PREFIX = 'libraries: ='


def get_clang_library_dirs(clang_executable_path: str) -> List[str]:
    search_dirs_cmd = [clang_executable_path, '-print-search-dirs']
    search_dirs_output = subprocess.check_output(search_dirs_cmd).decode('utf-8')
    library_dirs: Optional[List[str]] = None
    for line in search_dirs_output.split('\n'):
        line = line.strip()
        if line.startswith(LIBRARY_DIRS_PREFIX):
            library_dirs = [s.strip() for s in line[len(LIBRARY_DIRS_PREFIX):].split(':')]
            break
    if library_dirs is None:
        raise ValueError(
            f"Could not find a line starting with '{LIBRARY_DIRS_PREFIX}' in the "
            f"output of the command: {shlex_join(search_dirs_cmd)}:\n{search_dirs_output}")
    return library_dirs


def get_clang_library_dir(clang_executable_path: str) -> str:
    library_dirs = get_clang_library_dirs(clang_executable_path)
    candidate_dirs: List[str] = []
    for library_dir in library_dirs:
        candidate_dir = os.path.join(library_dir, 'lib', 'linux')
        if os.path.isdir(candidate_dir):
            return candidate_dir
        candidate_dirs.append(candidate_dir)

    raise ValueError(
        f"Could not find a 'lib/linux' subdirectory in any of the library directories "
        f"returned by 'clang -print-search-dirs' (clang path: {clang_executable_path}):\n"
        f"{library_dirs}\n.Considered candidate directories:{candidate_dirs}")


def get_clang_include_dir(clang_executable_path: str) -> str:
    library_dirs = get_clang_library_dirs(clang_executable_path)
    for library_dir in library_dirs:
        include_dir = os.path.join(library_dir, 'include')
        if os.path.isdir(include_dir):
            return include_dir
    raise ValueError(
        f"Could not find a directory from {library_dirs} that has an 'include' subdirectory.")
