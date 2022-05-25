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
Checking that the executables and shared libraries we have built don't depend on any unexpected
shared libraries installed on this system.
"""

import os
import sys
import re
import subprocess
import platform
import logging

from sys_detection import is_macos, is_linux

from typing import List, Any, Set, Optional, Pattern
from yugabyte_db_thirdparty.custom_logging import log, fatal, heading
from yugabyte_db_thirdparty.util import YB_THIRDPARTY_DIR, capture_all_output
from yugabyte_db_thirdparty.macos import get_min_supported_macos_version
from build_definitions import BUILD_TYPES


IGNORED_EXTENSIONS = (
    '.a',
    '.la',
    '.pc',
    '.inc',
    '.h',
    '.hpp',
    '.cmake',
)

IGNORED_FILE_NAMES = set([
    'LICENSE',
    'krb5-send-pr',
])

IGNORED_DIR_SUFFIXES = (
    '/include/c++/v1',
    '/include/c++/v1/experimental',
    '/include/c++/v1/ext',
)

# We pass some environment variables to ldd.
LDD_ENV = {'LC_ALL': 'en_US.UTF-8'}

ALLOWED_SYSTEM_LIBRARIES = (
    # These libraries are part of glibc.
    'libc',
    'libdl',
    'libm',
    'libpthread',
    'libresolv',
    'librt',
    'libutil',
    # TODO: we should not really need libgcc_s as we should be using Clang's compiler-rt only.
    'libgcc_s',
    # When we use Linuxbrew, we can also see ld-linux-x86-64.so.2 in ldd output.
    'ld-linux',
)

SKIPPED_LDD_OUTPUT_PREFIXES = (
    'Unused ',
    'ldd: warning: ',
    'not a dynamic'
)

NEEDED_LIBS_TO_REMOVE = (
    'libatomic',
)

LIBCXX_NOT_FOUND = re.compile(r'^\tlibc[+][+][.]so[.][0-9]+ => not found')
SYSTEM_LIBRARY_RE = re.compile(
    r'^.* => /lib(?:64|/(?:x86_64|aarch64)-linux-gnu)/([^ /]+) .*$')


def compile_re_list(re_list: List[str]) -> Any:
    return re.compile("|".join(re_list))


def get_needed_libs(file_path: str) -> List[str]:
    return capture_all_output(
        ['patchelf', '--print-needed', file_path],
        allowed_exit_codes={1},
        extra_msg_on_nonzero_exit_code="Warning: could not determine libraries directly "
                                       f"needed by {file_path}")


def is_text_based_so_file(so_path: str) -> bool:
    # libc++.so is a text file containing this:
    # INPUT(libc++.so.1 -lunwind -lc++abi)
    # We can't analyze this kind of a file with ldd so we skip it.
    with open(so_path, 'rb') as input_file:
        first_bytes = input_file.read(64)
        return first_bytes.startswith(b'INPUT')


class LibTestBase:
    """
    Verify correct library paths are used in installed dynamically-linked executables and
    libraries. Also verify certain properies of static libraries, e.g. minimum supported macOS
    version.
    """

    tp_installed_dir: str
    lib_re_list: List[str]
    tool: str

    # A compiled regex containing almost all of the allowed patterns (except for an an optional
    # additional pattern).
    allowed_patterns: Pattern

    # To make sure that we log each allowed pattern no more than once.
    logged_allowed_patterns: Set[str]

    extra_allowed_shared_lib_paths: Set[str]

    # We collect all files to check in this list.
    files_to_check: List[str]

    allowed_system_libraries: Set[str]

    def __init__(self) -> None:
        self.tp_installed_dir = os.path.join(YB_THIRDPARTY_DIR, 'installed')
        self.lib_re_list = []
        self.logged_allowed_patterns = set()
        self.extra_allowed_shared_lib_paths = set()
        self.allowed_system_libraries = set(ALLOWED_SYSTEM_LIBRARIES)

    def allow_system_libstdcxx(self) -> None:
        self.allowed_system_libraries.add('libstdc++')

    def init_regex(self) -> None:
        self.allowed_patterns = compile_re_list(self.lib_re_list)

    def check_lib_deps(
            self,
            file_path: str,
            cmd_output: List[str],
            additional_allowed_pattern: Optional[Pattern] = None) -> bool:

        status = True
        for line in cmd_output:
            if (not self.allowed_patterns.match(line) and
                    not (additional_allowed_pattern is not None and
                         additional_allowed_pattern.match(line))):
                # Log the allowed patterns for easier debugging.
                for allowed_pattern in [self.allowed_patterns] + (
                    [additional_allowed_pattern] if additional_allowed_pattern else []
                ):
                    if allowed_pattern.pattern not in self.logged_allowed_patterns:
                        log("Allowed pattern: %s", allowed_pattern.pattern)
                        self.logged_allowed_patterns.add(allowed_pattern.pattern)

                if status:
                    log(file_path + ":")
                    status = False
                log("Bad path: %s", line)

        return status

    def check_libs_for_file(self, file_path: str) -> bool:
        """
        Checks if the given file's shared libraries resolve in a correct way. Overridden in
        OS-specific classes.
        """
        raise NotImplementedError()

    def should_check_file(self, file_path: str) -> bool:
        if (os.path.islink(file_path) or
                is_text_based_so_file(file_path) or
                file_path.endswith(IGNORED_EXTENSIONS) or
                os.path.basename(file_path) in IGNORED_FILE_NAMES):
            return False

        file_dir = os.path.dirname(file_path)
        return not any(file_dir.endswith(suffix) for suffix in IGNORED_DIR_SUFFIXES)

    def run(self) -> None:
        self.init_regex()
        heading("Scanning installed executables and libraries...")
        for allowed_shared_lib_path in sorted(self.extra_allowed_shared_lib_paths):
            log("Extra allowed shared lib path: %s", allowed_shared_lib_path)
        test_pass = True
        # Files to examine are much reduced if we look only at bin and lib directories.
        dir_pattern = re.compile('^(lib|libcxx|[s]bin)$')
        dirs = [os.path.join(self.tp_installed_dir, type) for type in BUILD_TYPES]

        self.files_to_check = []
        for installed_dir in dirs:
            if not os.path.isdir(installed_dir):
                logging.info("Directory %s does not exist, skipping", installed_dir)
                continue
            with os.scandir(installed_dir) as candidate_dirs:
                for candidate in candidate_dirs:
                    if dir_pattern.match(candidate.name):
                        examine_path = os.path.join(installed_dir, candidate.name)
                        for dirpath, dir_names, files in os.walk(examine_path):
                            for file_name in files:
                                full_path = os.path.join(dirpath, file_name)
                                if not self.should_check_file(full_path):
                                    continue
                                self.files_to_check.append(full_path)

        self.before_checking_all_files()
        test_pass = self.check_all_files()

        if not test_pass:
            fatal(f"Found problematic library dependencies, using tool: {self.tool}")
        else:
            log("No problems found with library dependencies.")

    def before_checking_all_files(self) -> None:
        pass

    def check_all_files(self) -> bool:
        success = True
        for file_path in self.files_to_check:
            if not self.check_libs_for_file(file_path):
                # We are not returning here because we want to log all errors.
                success = False
        return success

    def add_allowed_shared_lib_paths(self, shared_lib_paths: Set[str]) -> None:
        self.extra_allowed_shared_lib_paths |= shared_lib_paths


class LibTestMac(LibTestBase):
    def __init__(self) -> None:
        super().__init__()
        self.tool = "otool -L"
        self.lib_re_list = [
            "^\t/System/Library/",
            "^Archive ",
            "^/",
            "^\t@rpath",
            "^\t@loader_path",
            f"^\t{YB_THIRDPARTY_DIR}",
            # We don't allow to use libraries from /usr/local/... because Homebrew libraries are
            # installed there and we try to rely on as few of those as possible.
            "^\t/usr/lib/",
        ]

    def check_libs_for_file(self, file_path: str) -> bool:
        otool_output = subprocess.check_output(['otool', '-L', file_path]).decode('utf-8')
        if 'is not an object file' in otool_output:
            return True

        if not self.check_lib_deps(file_path, otool_output.splitlines()):
            return False

        min_supported_macos_version = get_min_supported_macos_version()

        # Additionally, check for the minimum macOS version encoded in the library file.
        otool_small_l_output = subprocess.check_output(['otool', '-l', file_path]).decode('utf-8')
        section = ""
        for line in otool_small_l_output.split('\n'):
            line = line.strip()
            if line.endswith(':'):
                section = line
            if line.startswith('minos '):
                items = line.split()
                min_macos_version = items[1]
                if min_macos_version != min_supported_macos_version:
                    log("File %s has wrong minimum supported macOS version: %s. Full line:\n%s\n"
                        "(output from 'otool -l'). Expected: %s, section: %s",
                        file_path, min_macos_version, line, min_supported_macos_version,
                        section)
                    return False

        return True


class LibTestLinux(LibTestBase):
    def __init__(self) -> None:
        super().__init__()
        self.tool = "ldd"
        self.lib_re_list = [
            "^\tlinux-vdso",
            "^\t/lib64/",
            "^\t/lib/ld-linux-.*",
            "^\t/opt/yb-build/brew/linuxbrew",
            "^\tstatically linked",
            "^\tnot a dynamic executable",
            "ldd: warning: you do not have execution permission",
            "^.* => /lib64/",
            "^.* => /lib/",
            "^.* => /usr/lib/x86_64-linux-gnu/",
            "^.* => /opt/yb-build/brew/linuxbrew",
            f"^.* => {re.escape(YB_THIRDPARTY_DIR)}"
        ]

    def add_allowed_shared_lib_paths(self, shared_lib_paths: Set[str]) -> None:
        super().add_allowed_shared_lib_paths(shared_lib_paths)
        for shared_lib_path in sorted(shared_lib_paths):
            self.lib_re_list.append(f".* => {re.escape(shared_lib_path)}/")

    def before_checking_all_files(self) -> None:
        for file_path in self.files_to_check:
            self.fix_needed_libs_for_file(file_path)

    def fix_needed_libs_for_file(self, file_path: str) -> None:
        needed_libs: List[str] = get_needed_libs(file_path)

        if needed_libs:
            ldd_u_output_lines: List[str] = capture_all_output(
                ['ldd', '-u', file_path],
                allowed_exit_codes={1})
            removed_libs: List[str] = []
            for ldd_u_output_line in ldd_u_output_lines:
                ldd_u_output_line = ldd_u_output_line.strip()
                if ldd_u_output_line.startswith('Inconsistency'):
                    raise IOError(f'ldd -u failed on file {file_path}: {ldd_u_output_line}')
                if ldd_u_output_line.startswith(SKIPPED_LDD_OUTPUT_PREFIXES):
                    continue
                unused_lib_path = ldd_u_output_line

                if not os.path.exists(unused_lib_path):
                    raise IOError("File does not exist: %s" % unused_lib_path)
                unused_lib_name = os.path.basename(unused_lib_path)
                if unused_lib_name not in needed_libs:
                    raise ValueError(
                        "Unused library %s does not match the list of needed libs: %s" % (
                            unused_lib_path, needed_libs))
                if any([unused_lib_name.startswith(lib_name + '.')
                        for lib_name in NEEDED_LIBS_TO_REMOVE]):
                    subprocess.check_call([
                        'patchelf',
                        '--remove-needed',
                        unused_lib_name,
                        file_path
                    ])
                    log("Removed unused needed lib %s from %s", unused_lib_name, file_path)
                    removed_libs.append(unused_lib_name)
            new_needed_libs = get_needed_libs(file_path)
            for removed_lib in removed_libs:
                if removed_lib in new_needed_libs:
                    raise ValueError(f"Failed to remove needed library {removed_lib} from "
                                     f"{file_path}. File's current needed libs: {new_needed_libs}")

    def is_allowed_system_lib(self, lib_name: str) -> bool:
        return any(lib_name.startswith(
            (allowed_lib_name + '.', allowed_lib_name + '-'))
            for allowed_lib_name in self.allowed_system_libraries)

    def check_libs_for_file(self, file_path: str) -> bool:
        file_basename = os.path.basename(file_path)
        additional_allowed_pattern = None
        if file_basename.startswith('libc++abi.so.'):
            # One exception: libc++abi.so is not able to find libc++ because it loads the ASAN
            # runtime library that is part of the LLVM distribution and does not have the correct
            # rpath set. This happens on CentOS with our custom build of LLVM. We might be able to
            # fix this by specifying rpath correctly when building LLVM, but as of 12/2020 we just
            # ignore this error here.
            #
            # $ ldd installed/asan/libcxx/lib/libc++abi.so.1.0
            #   linux-vdso.so.1 =>
            #   libclang_rt.asan-x86_64.so =>
            #     $LLVM_DIR/lib/clang/11.0.0/lib/linux/libclang_rt.asan-x86_64.so
            #   libclang_rt.ubsan_minimal-x86_64.so =>
            #     $LLVM_DIR/lib/clang/11.0.0/lib/linux/libclang_rt.ubsan_minimal-x86_64.so
            #   libunwind.so.1 => installed/common/lib/libunwind.so.1
            #   libdl.so.2 => /lib64/libdl.so.2
            #   libpthread.so.0 => /lib64/libpthread.so.0
            #   libm.so.6 => /lib64/libm.so.6
            #   libc.so.6 => /lib64/libc.so.6
            #   libc++.so.1 => not found  <-- THIS IS OK
            #   libgcc_s.so.1 => /lib64/libgcc_s.so.1
            #   librt.so.1 => /lib64/librt.so.1
            #   /lib64/ld-linux-x86-64.so.2
            #
            # Run
            #   LD_DEBUG=all ldd installed/asan/libcxx/lib/libc++abi.so.1.0
            # and notice the following line:
            #
            # file=libc++.so.1 [0];
            #   needed by $LLVM_DIR/lib/clang/11.0.0/lib/linux/libclang_rt.asan-x86_64.so
            #
            # Also running
            #   ldd $LLVM_DIR/lib/clang/11.0.0/lib/linux/libclang_rt.asan-x86_64.so
            #
            # reports "libc++.so.1 => not found".
            additional_allowed_pattern = LIBCXX_NOT_FOUND

        # After we potentially removed some of the
        ldd_output_lines: List[str] = capture_all_output(
            ['ldd', file_path],
            env=LDD_ENV,
            allowed_exit_codes={1})

        if any(['not a dynamic executable' in line for line in ldd_output_lines]):
            return True

        success = True
        for line in ldd_output_lines:
            match = SYSTEM_LIBRARY_RE.search(line.strip())
            if match:
                system_lib_name = match.group(1)
                if not self.is_allowed_system_lib(system_lib_name):
                    log("Disallowed system library: %s. Allowed: %s. File: %s",
                        system_lib_name, sorted(self.allowed_system_libraries), file_path)
                    success = False

        return self.check_lib_deps(
            file_path, ldd_output_lines, additional_allowed_pattern) and success


def get_lib_tester() -> LibTestBase:
    if is_macos():
        return LibTestMac()
    if is_linux():
        return LibTestLinux()

    fatal(f"Unsupported platform: {platform.system()}")
