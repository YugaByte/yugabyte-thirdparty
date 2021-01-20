import os

from yugabyte_db_thirdparty.download_manager import DownloadManager
from yugabyte_db_thirdparty.util import YB_THIRDPARTY_DIR, write_file


LINUXBREW_URL = (
    'https://github.com/yugabyte/brew-build/releases/download/'
    '20181203T161736v9/linuxbrew-20181203T161736v9.tar.gz'
)

LLVM11_CENTOS7_URL = (
    'https://github.com/yugabyte/build-clang/releases/download/'
    'v11.0.0-1607398732/yb-llvm-v11.0.0-1607398732.tar.gz'
)

TOOLCHAIN_TYPE_TO_URL = {
    'linuxbrew': LINUXBREW_URL,
    'llvm11': LLVM11_CENTOS7_URL
}

TOOLCHAIN_TYPES = sorted(TOOLCHAIN_TYPE_TO_URL.keys())


class Toolchain:
    toolchain_type: str
    toolchain_root: str

    def __init__(
            self,
            toolchain_url: str,
            toolchain_type: str,
            toolchain_root: str) -> None:
        self.toolchain_url = toolchain_url
        self.toolchain_type = toolchain_type
        self.toolchain_root = toolchain_root

    def get_compiler_type(self) -> str:
        for compiler_type_candidate in ['clang', 'gcc']:
            if os.path.exists(
                    os.path.join(self.toolchain_root, 'bin', compiler_type_candidate)):
                return compiler_type_candidate
        raise RuntimeError(
            f"Cannot determine compiler type for toolchain at '{self.toolchain_root}'")

    def write_url_and_path_files(self) -> None:
        write_file(os.path.join(YB_THIRDPARTY_DIR, 'toolchain_url.txt'),
                   self.toolchain_url)
        write_file(os.path.join(YB_THIRDPARTY_DIR, 'toolchain_path.txt'),
                   self.toolchain_root)
        if self.toolchain_type == 'linuxbrew':
            # TODO: remove this after the YugabyteDB build system is upgraded to only look at
            # toolchain_{url,path}.txt.
            write_file(os.path.join(YB_THIRDPARTY_DIR, 'linuxbrew_url.txt'),
                       self.toolchain_url)
            write_file(os.path.join(YB_THIRDPARTY_DIR, 'linuxbrew_path.txt'),
                       self.toolchain_root)


def ensure_toolchain_installed(
        download_manager: DownloadManager,
        toolchain_type: str) -> Toolchain:
    assert toolchain_type in TOOLCHAIN_TYPES, (
        f"Invalid toolchain type: '{toolchain_type}'. Valid types: "
        f"{', '.join(TOOLCHAIN_TYPES)}."
    )

    toolchain_url = TOOLCHAIN_TYPE_TO_URL[toolchain_type]
    compiler_type = None
    if toolchain_type.startswith('llvm'):
        parent_dir = '/opt/yb-build/llvm'
    elif toolchain_type == 'linuxbrew':
        parent_dir = '/opt/yb-build/brew'
    else:
        raise RuntimeError(
            f"We don't know where to install toolchain of type f{toolchain_type}")

    toolchain_root = download_manager.download_toolchain(toolchain_url, parent_dir)

    return Toolchain(
        toolchain_url=toolchain_url,
        toolchain_type=toolchain_type,
        toolchain_root=toolchain_root)
