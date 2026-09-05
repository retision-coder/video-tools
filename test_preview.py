# -*- coding: utf-8 -*-
"""处理过程实时对比预览测试：
1) process_video 写出预览图（源帧≠处理帧，水印区确实有变化）
2) GUI 全流程：开始处理 → 对比视图收到实时帧 → 结束后恢复框选预览页
"""
import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# 隔离配置目录，避免读写真实 %APPDATA% 下用户配置
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="wme_test_")

import cv2
import main
from PyQt5.QtWidgets import QApplication, QMessageBox
from PyQt5.QtCore import QTimer

QMessageBox.exec_ = lambda self: 0
QMessageBox.information = staticmethod(lambda *a, **k: 0)
QMessageBox.warning = staticmethod(lambda *a, **k: 0)
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)

SRC = os.path.abspath("test_input3.mp4")  # 640×360@25fps，水印在 (455,10,180,50)


def test_unit_preview_dir():
    """process_video 的 preview_dir：产出 src.jpg/dst.jpg 且内容有差异。"""
    pv = tempfile.mkdtemp(prefix="wme_pv_unit_")
    outp = os.path.join(tempfile.gettempdir(), "wme_pv_unit.mp4")
    regions = [{"mode": "blur", "keys": [(0.0, (455, 10, 180, 50))]}]
    ok, msg = main.process_video(main.get_ffmpeg(), SRC, outp, regions,
                                 preview_dir=pv)
    assert ok, msg
    src_p = os.path.join(pv, "src.jpg")
    dst_p = os.path.join(pv, "dst.jpg")
    assert os.path.isfile(src_p) and os.path.isfile(dst_p), "预览图未生成"
    a = cv2.imread(src_p)
    b = cv2.imread(dst_p)
    assert a is not None and b is not None and a.shape == b.shape
    # 水印区域在两图之间必须有明显差异（被模糊处理了）
    diff = cv2.absdiff(a[10:60, 455:635], b[10:60, 455:635]).mean()
    assert diff > 5, f"水印区差异过小：{diff}"
    print(f"ok: 预览图生成，水印区平均差异 {diff:.1f}")
    return True


def test_gui_compare_view():
    """GUI：处理中对比视图实时刷新，结束后恢复框选预览页。"""
    orig_exec = QApplication.exec_

    def patched_exec():
        app = QApplication.instance()

        def flow():
            wl = [x for x in app.topLevelWidgets()
                  if x.__class__.__name__ == "MainWindow"]
            if not wl:
                print("FAIL: MainWindow 未创建")
                app.quit()
                return
            w = wl[0]
            w.video_path = SRC
            w.duration = main.get_duration(w.ffmpeg, SRC)
            w._request_frame(0.0)

            def begin():
                try:
                    regions = [{"mode": "blur",
                                "keys": [(0.0, (455.0, 10.0, 180.0, 50.0))]}]
                    outp = os.path.join(tempfile.gettempdir(),
                                        "wme_pv_gui_out.mp4")
                    w._begin_process(regions, outp)
                    # 处理中：应切到对比视图页
                    assert w.stack.currentWidget() is w.compare, "未切到对比视图"
                    assert w._pv_dir and os.path.isdir(w._pv_dir), "预览目录未建"
                    assert w._pv_timer.isActive(), "预览轮询未启动"
                    # 等待处理完成（事件循环持续泵送，轮询定时器会刷新对比图）
                    deadline = time.monotonic() + 180
                    got_frames = False
                    while w.worker is not None and time.monotonic() < deadline:
                        app.processEvents()
                        time.sleep(0.05)
                        if w._pv_mtimes:
                            got_frames = True
                    assert got_frames, "对比视图未收到任何实时帧"
                    assert w.worker is None, "处理超时未结束"
                    # 结束后：恢复框选预览页、轮询停止、目录清理
                    assert w.stack.currentWidget() is w.preview, "未恢复预览页"
                    assert not w._pv_timer.isActive(), "轮询未停止"
                    assert w._pv_dir is None, "预览目录未清理"
                    print("ok: 对比视图实时刷新 → 结束后恢复框选预览")
                except Exception:
                    import traceback
                    traceback.print_exc()
                finally:
                    app.quit()

            QTimer.singleShot(300, begin)

        QTimer.singleShot(100, flow)
        return orig_exec()

    QApplication.exec_ = staticmethod(patched_exec)
    sys.exit = lambda *a: None
    main.main()
    return True


if __name__ == "__main__":
    ok1 = test_unit_preview_dir()
    ok2 = test_gui_compare_view()
    print("PREVIEW TEST DONE" if (ok1 and ok2) else "PREVIEW TEST FAILED")
    sys.exit(0 if (ok1 and ok2) else 1)
