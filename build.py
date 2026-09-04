# -*- coding: utf-8 -*-
"""打包脚本：把 main.py 打成单文件 exe，并内置 ffmpeg 二进制。

版本资源 version_info.txt 由 version.py 自动生成（单一版本来源），
不纳入 git 管理。
"""
import os
import subprocess
import sys

import imageio_ffmpeg

from version import APP_NAME, APP_VERSION

HERE = os.path.dirname(os.path.abspath(__file__))
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
APP_EXE = APP_NAME + ".exe"

VERSION_INFO_TPL = """# UTF-8
# PyInstaller 版本资源：由 build.py 根据 version.py 自动生成，请勿手改
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({maj}, {min}, {pat}, 0),
    prodvers=({maj}, {min}, {pat}, 0),
    mask=0x3F,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '080404B0',
        [StringStruct('CompanyName', 'VideoTools'),
         StringStruct('FileDescription', '{name}'),
         StringStruct('FileVersion', '{ver}.0'),
         StringStruct('InternalName', 'WatermarkEraser'),
         StringStruct('LegalCopyright', ''),
         StringStruct('OriginalFilename', '{exe}'),
         StringStruct('ProductName', '{name}'),
         StringStruct('ProductVersion', '{ver}.0')])
    ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
"""


def write_version_info():
    """按 version.py 里的版本号生成 PyInstaller 版本资源文件。"""
    maj, min_, pat = (int(v) for v in APP_VERSION.split(".")[:3])
    path = os.path.join(HERE, "version_info.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(VERSION_INFO_TPL.format(maj=maj, min=min_, pat=pat,
                                        name=APP_NAME, exe=APP_EXE,
                                        ver=APP_VERSION))
    return path


cmd = [
    sys.executable, "-m", "PyInstaller",
    "--noconfirm", "--clean",
    "--onefile", "--windowed",
    "--name", APP_NAME,
    f"--version-file={write_version_info()}",
    f"--add-binary={FFMPEG};imageio_ffmpeg/binaries",
    # AI 引擎脚本及其共享依赖，运行时复制到干净目录执行
    f"--add-data={os.path.join(HERE, 'ai_engine.py')};.",
    f"--add-data={os.path.join(HERE, 'wm_core.py')};.",
    os.path.join(HERE, "main.py"),
]
print("ffmpeg:", FFMPEG)
print(" ".join(cmd))
subprocess.check_call(cmd, cwd=HERE)
print("\n打包完成：", os.path.join(HERE, "dist", APP_EXE))
