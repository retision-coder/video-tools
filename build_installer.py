# -*- coding: utf-8 -*-
"""打包安装程序：把主程序 exe 内嵌进 setup.py，生成单文件安装包。"""
import os
import subprocess
import sys

from version import APP_NAME, APP_VERSION

HERE = os.path.dirname(os.path.abspath(__file__))
APP_EXE = os.path.join(HERE, "dist", APP_NAME + ".exe")
OUT_NAME = f"{APP_NAME}_Setup_v{APP_VERSION}"

if not os.path.isfile(APP_EXE):
    sys.exit("请先运行 build.py 生成主程序 exe")

cmd = [
    sys.executable, "-m", "PyInstaller",
    "--noconfirm", "--clean",
    "--onefile", "--windowed",
    "--name", OUT_NAME,
    "--distpath", os.path.join(HERE, "installer"),
    "--workpath", os.path.join(HERE, "build_setup"),
    "--specpath", HERE,
    f"--add-data={APP_EXE};app_payload",
    os.path.join(HERE, "setup.py"),
]
print(" ".join(cmd))
subprocess.check_call(cmd, cwd=HERE)
print("\n安装包完成：", os.path.join(HERE, "installer", OUT_NAME + ".exe"))
