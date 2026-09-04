# -*- coding: utf-8 -*-
"""
视频水印擦除工具 · 安装程序
---------------------------
单文件安装器（内嵌主程序，无需联网、无需管理员权限）：
- 图形向导：选择安装目录 → 安装 → 桌面/开始菜单快捷方式
- 静默安装（供以后在线升级调用）：Setup.exe /S [/D=安装目录]
- 安装后在"应用与功能"中可见，自带卸载程序（卸载.exe，支持 /S 静默卸载）
- 主程序用户配置（水印方案）保存在 %APPDATA%，升级/卸载不受影响
"""
import os
import shutil
import subprocess
import sys
import threading

APP_NAME = "视频水印擦除工具"
APP_VERSION = "1.5.1"
APP_EXE = "视频水印擦除工具.exe"
UNINST_EXE = "卸载.exe"
REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\VideoWatermarkEraser"

_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def default_install_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "Programs", "videotools")


def existing_install():
    """从注册表读取已安装版本的信息：(安装目录, 版本号)；未安装返回 None。"""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY) as k:
            instdir, _ = winreg.QueryValueEx(k, "InstallLocation")
            try:
                ver, _ = winreg.QueryValueEx(k, "DisplayVersion")
            except OSError:
                ver = ""
        if instdir and os.path.isfile(os.path.join(instdir, APP_EXE)):
            return instdir, ver
    except OSError:
        pass
    return None


def preferred_install_dir():
    """升级时默认装到现有安装目录；全新安装用默认目录。"""
    ex = existing_install()
    return ex[0] if ex else default_install_dir()


def payload_path():
    """安装包内嵌的主程序路径。"""
    if getattr(sys, "frozen", False):
        p = os.path.join(sys._MEIPASS, "app_payload", APP_EXE)
    else:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "dist", APP_EXE)
    return p


def create_shortcut(lnk_path, target, workdir=None):
    """用 COM (WScript.Shell) 创建快捷方式，不依赖外部进程。"""
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    try:
        ws = win32com.client.Dispatch("WScript.Shell")
        s = ws.CreateShortcut(lnk_path)
        s.TargetPath = target
        s.WorkingDirectory = workdir or os.path.dirname(target)
        s.Save()
    finally:
        pythoncom.CoUninitialize()


def register_uninstall(instdir):
    import winreg
    uninst = os.path.join(instdir, UNINST_EXE)
    size_kb = 0
    try:
        size_kb = os.path.getsize(os.path.join(instdir, APP_EXE)) // 1024
    except Exception:
        pass
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_KEY) as k:
        winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
        winreg.SetValueEx(k, "DisplayVersion", 0, winreg.REG_SZ, APP_VERSION)
        winreg.SetValueEx(k, "Publisher", 0, winreg.REG_SZ, "VideoTools")
        winreg.SetValueEx(k, "InstallLocation", 0, winreg.REG_SZ, instdir)
        winreg.SetValueEx(k, "DisplayIcon", 0, winreg.REG_SZ,
                          os.path.join(instdir, APP_EXE))
        winreg.SetValueEx(k, "UninstallString", 0, winreg.REG_SZ,
                          f'"{uninst}"')
        winreg.SetValueEx(k, "QuietUninstallString", 0, winreg.REG_SZ,
                          f'"{uninst}" /S')
        winreg.SetValueEx(k, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "NoRepair", 0, winreg.REG_DWORD, 1)
        if size_kb:
            winreg.SetValueEx(k, "EstimatedSize", 0, winreg.REG_DWORD,
                              int(size_kb))


def unregister_uninstall():
    import winreg
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, REG_KEY)
    except OSError:
        pass


def install(instdir, desktop=True, startmenu=True, log=print):
    """执行安装。返回安装目录。异常向上抛。"""
    src = payload_path()
    if not os.path.isfile(src):
        raise RuntimeError(f"安装包内容缺失：{src}")
    os.makedirs(instdir, exist_ok=True)
    log(f"复制主程序 → {instdir}")
    shutil.copy2(src, os.path.join(instdir, APP_EXE))

    log("写入卸载程序")
    if getattr(sys, "frozen", False):
        shutil.copy2(sys.executable, os.path.join(instdir, UNINST_EXE))
    else:
        # 源码调试模式：放一个占位脚本
        with open(os.path.join(instdir, UNINST_EXE + ".txt"), "w",
                  encoding="utf-8") as f:
            f.write("源码调试模式无真实卸载程序")

    if desktop:
        log("创建桌面快捷方式")
        create_shortcut(os.path.join(os.path.expanduser("~"), "Desktop",
                                     APP_NAME + ".lnk"),
                        os.path.join(instdir, APP_EXE), instdir)
    if startmenu:
        sm = os.path.join(os.environ.get("APPDATA", ""),
                          r"Microsoft\Windows\Start Menu\Programs", APP_NAME)
        os.makedirs(sm, exist_ok=True)
        log("创建开始菜单快捷方式")
        create_shortcut(os.path.join(sm, APP_NAME + ".lnk"),
                        os.path.join(instdir, APP_EXE), instdir)
        create_shortcut(os.path.join(sm, "卸载 " + APP_NAME + ".lnk"),
                        os.path.join(instdir, UNINST_EXE), instdir)

    log("写入注册信息")
    register_uninstall(instdir)
    log("安装完成")
    return instdir


def _self_delete_and_rmdir(instdir):
    """退出后删除卸载程序自身并清理空目录（仅冻结 exe 模式）。"""
    if not getattr(sys, "frozen", False):
        return
    me = os.path.abspath(sys.executable)
    cmd = (f'cmd /c ping 127.0.0.1 -n 3 >nul & del /f /q "{me}" '
           f'& rmdir "{instdir}"')
    subprocess.Popen(cmd, shell=True,
                     creationflags=_CREATE_NO_WINDOW | 0x00000008,
                     close_fds=True)


def uninstall(instdir, log=print):
    """执行卸载（不删除 %APPDATA% 中的用户方案配置）。"""
    app = os.path.join(instdir, APP_EXE)
    if os.path.isfile(app):
        log("删除主程序")
        os.remove(app)
    uninst_txt = os.path.join(instdir, UNINST_EXE + ".txt")
    if os.path.isfile(uninst_txt):
        os.remove(uninst_txt)
    log("删除快捷方式")
    lnk = os.path.join(os.path.expanduser("~"), "Desktop", APP_NAME + ".lnk")
    if os.path.isfile(lnk):
        os.remove(lnk)
    sm = os.path.join(os.environ.get("APPDATA", ""),
                      r"Microsoft\Windows\Start Menu\Programs", APP_NAME)
    if os.path.isdir(sm):
        shutil.rmtree(sm, ignore_errors=True)
    log("清除注册信息")
    unregister_uninstall()
    log("卸载完成")
    _self_delete_and_rmdir(instdir)


def is_uninstall_mode(argv):
    exe_name = os.path.basename(sys.executable if getattr(sys, "frozen", False)
                                else argv[0])
    return ("--uninstall" in argv or "/uninstall" in argv
            or exe_name.startswith("卸载"))


def parse_silent(argv):
    silent = any(a.lower() in ("/s", "--silent") for a in argv[1:])
    instdir = None
    for a in argv[1:]:
        if a.upper().startswith("/D="):
            instdir = a[3:].strip('"')
        elif a.startswith("--dir="):
            instdir = a[6:].strip('"')
    return silent, instdir


# ---------------------------------------------------------------------------
# GUI 向导
# ---------------------------------------------------------------------------

def gui_wizard():
    from PyQt5.QtCore import QThread, pyqtSignal
    from PyQt5.QtWidgets import (QApplication, QCheckBox, QDialog,
                                 QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                                 QMessageBox, QProgressBar, QPushButton,
                                 QVBoxLayout)

    class InstallThread(QThread):
        logline = pyqtSignal(str)
        done = pyqtSignal(bool, str)

        def __init__(self, instdir, desktop, startmenu):
            super().__init__()
            self.args = (instdir, desktop, startmenu)

        def run(self):
            try:
                install(*self.args, log=self.logline.emit)
                self.done.emit(True, self.args[0])
            except Exception as e:
                self.done.emit(False, str(e))

    class Wizard(QDialog):
        def __init__(self):
            super().__init__()
            self.setWindowTitle(f"{APP_NAME} 安装向导 v{APP_VERSION}")
            self.setFixedWidth(480)
            v = QVBoxLayout(self)

            ex = existing_install()
            if ex:
                v.addWidget(QLabel(
                    f"<h3>升级 {APP_NAME}</h3>"
                    f"<p>检测到已安装 v{ex[1] or '未知版本'}（{ex[0]}），<br>"
                    f"本次将覆盖升级到 <b>v{APP_VERSION}</b>，"
                    "你的水印方案配置会保留。</p>"))
            else:
                v.addWidget(QLabel(
                    f"<h3>欢迎使用 {APP_NAME} 安装向导</h3>"
                    "<p>将在你的电脑上安装视频水印擦除工具（无需管理员权限）。</p>"))
            v.addWidget(QLabel("安装位置："))
            row = QHBoxLayout()
            self.ed_dir = QLineEdit(ex[0] if ex else default_install_dir())
            btn = QPushButton("浏览…")
            btn.clicked.connect(self._browse)
            row.addWidget(self.ed_dir, 1)
            row.addWidget(btn)
            v.addLayout(row)
            self.ck_desktop = QCheckBox("创建桌面快捷方式")
            self.ck_desktop.setChecked(True)
            self.ck_startmenu = QCheckBox("添加到开始菜单")
            self.ck_startmenu.setChecked(True)
            v.addWidget(self.ck_desktop)
            v.addWidget(self.ck_startmenu)

            self.progress = QProgressBar()
            self.progress.setRange(0, 0)
            self.progress.setVisible(False)
            v.addWidget(self.progress)
            self.lbl_log = QLabel("")
            self.lbl_log.setStyleSheet("color:#666;")
            v.addWidget(self.lbl_log)

            arow = QHBoxLayout()
            arow.addStretch(1)
            self.btn_install = QPushButton("安装")
            self.btn_install.setMinimumWidth(100)
            self.btn_install.setStyleSheet(
                "QPushButton{background:#2d8cf0;color:white;font-weight:bold;"
                "border-radius:4px;padding:6px;}")
            self.btn_install.clicked.connect(self._install)
            self.btn_cancel = QPushButton("取消")
            self.btn_cancel.clicked.connect(self.reject)
            arow.addWidget(self.btn_install)
            arow.addWidget(self.btn_cancel)
            v.addLayout(arow)
            self._instdir = None

        def _browse(self):
            d = QFileDialog.getExistingDirectory(self, "选择安装目录",
                                                 self.ed_dir.text())
            if d:
                self.ed_dir.setText(d)

        def _install(self):
            instdir = self.ed_dir.text().strip() or default_install_dir()
            self.btn_install.setEnabled(False)
            self.progress.setVisible(True)
            self.th = InstallThread(instdir, self.ck_desktop.isChecked(),
                                    self.ck_startmenu.isChecked())
            self.th.logline.connect(self.lbl_log.setText)
            self.th.done.connect(self._done)
            self.th.start()

        def _done(self, ok, msg):
            self.progress.setRange(0, 100)
            self.progress.setValue(100 if ok else 0)
            if ok:
                self._instdir = msg
                self.lbl_log.setText("安装完成！")
                self.btn_install.setText("完成并运行")
                self.btn_install.setEnabled(True)
                self.btn_install.clicked.disconnect()
                self.btn_install.clicked.connect(self._launch)
            else:
                self.lbl_log.setText("安装失败：" + msg)
                QMessageBox.warning(self, APP_NAME, "安装失败：" + msg)
                self.btn_install.setEnabled(True)

        def _launch(self):
            try:
                os.startfile(os.path.join(self._instdir, APP_EXE))
            except Exception:
                pass
            self.accept()

    app = QApplication(sys.argv)
    w = Wizard()
    w.exec_()


def gui_uninstall(instdir):
    from PyQt5.QtWidgets import QApplication, QMessageBox
    app = QApplication(sys.argv)
    ret = QMessageBox.question(
        None, APP_NAME,
        f"确定要卸载 {APP_NAME} 吗？\n\n安装目录：{instdir}\n"
        "（你保存的水印方案配置会保留，重新安装后仍可用）")
    if ret == QMessageBox.Yes:
        try:
            uninstall(instdir)
            QMessageBox.information(None, APP_NAME, "卸载完成。")
        except Exception as e:
            QMessageBox.warning(None, APP_NAME, "卸载失败：" + str(e))


def _crash_log(exe_dir):
    import traceback
    try:
        with open(os.path.join(exe_dir, "setup_crash.log"), "w",
                  encoding="utf-8") as f:
            traceback.print_exc(file=f)
    except Exception:
        pass


def main():
    argv = sys.argv
    silent, instdir = parse_silent(argv)
    exe_dir = os.path.dirname(os.path.abspath(
        sys.executable if getattr(sys, "frozen", False) else __file__))

    try:
        if is_uninstall_mode(argv):
            instdir = instdir or exe_dir
            if silent:
                try:
                    uninstall(instdir, log=lambda m: None)
                    sys.exit(0)
                except Exception:
                    sys.exit(1)
            gui_uninstall(instdir)
            return

        if silent:
            instdir = instdir or preferred_install_dir()
            log_path = os.path.join(exe_dir, "install.log")
            lines = []
            try:
                install(instdir, log=lines.append)
                lines.append(f"INSTALLED_TO={instdir}")
                code = 0
            except Exception as e:
                lines.append("ERROR=" + str(e))
                code = 1
            try:
                with open(log_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(lines))
            except Exception:
                pass
            sys.exit(code)

        gui_wizard()
    except SystemExit:
        raise
    except Exception:
        _crash_log(exe_dir)
        raise


if __name__ == "__main__":
    main()
