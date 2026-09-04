# -*- coding: utf-8 -*-
"""打包脚本：把 main.py 打成单文件 exe，并内置 ffmpeg 二进制。"""
import os
import subprocess
import sys

import imageio_ffmpeg

HERE = os.path.dirname(os.path.abspath(__file__))
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

cmd = [
    sys.executable, "-m", "PyInstaller",
    "--noconfirm", "--clean",
    "--onefile", "--windowed",
    "--name", "视频水印擦除工具",
    f"--version-file={os.path.join(HERE, 'version_info.txt')}",
    f"--add-binary={FFMPEG};imageio_ffmpeg/binaries",
    f"--add-data={os.path.join(HERE, 'ai_engine.py')};.",
    os.path.join(HERE, "main.py"),
]
print("ffmpeg:", FFMPEG)
print(" ".join(cmd))
subprocess.check_call(cmd, cwd=HERE)
print("\n打包完成：", os.path.join(HERE, "dist", "视频水印擦除工具.exe"))
