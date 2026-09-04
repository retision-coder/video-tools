# -*- coding: utf-8 -*-
"""播放控制 + 帧级定位 + 自动关键帧 冒烟测试（offscreen）。"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="wme_test_")

import main
from PyQt5.QtWidgets import QApplication, QMessageBox, QInputDialog, QFileDialog
from PyQt5.QtCore import QTimer, QRect

QMessageBox.exec_ = lambda self: 0
QMessageBox.information = staticmethod(lambda *a, **k: 0)
QMessageBox.warning = staticmethod(lambda *a, **k: 0)
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
QInputDialog.getText = staticmethod(lambda *a, **k: ("方案X", True))

orig_exec = QApplication.exec_
VIDEO = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "test_input3.mp4")


def patched_exec():
    app = QApplication.instance()

    def flow():
        try:
            flow_body()
        except Exception:
            import traceback
            traceback.print_exc()
            app.quit()

    def flow_body():
        mw = [w for w in app.topLevelWidgets()
              if w.__class__.__name__ == "MainWindow"]
        if not mw:
            print("FAIL: MainWindow 未创建")
            return
        w = mw[0]
        QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (VIDEO, ""))
        w.open_video()
        print("fps:", w.fps, "frames:", w.total_frames,
              "dur: %.2f" % w.duration, "size:", w.vw, "x", w.vh)
        assert w._cap is not None, "cv2 应能打开测试视频"
        assert w.total_frames > 0 and w.fps > 1

        # --- 帧级定位 ---
        w._seek_frame(10)
        assert w._cur_f == 10, f"seek 到第 10 帧失败: {w._cur_f}"
        w._step_frame(1)
        assert w._cur_f == 11, f"下一帧失败: {w._cur_f}"
        w._step_frame(-1)
        assert w._cur_f == 10, f"上一帧失败: {w._cur_f}"
        print("frame step ok, cur:", w._cur_f, "t=%.3f" % w._cur_t)

        # --- 自动关键帧 ---
        w.cmb_preset.activated.emit(2)  # 右上角新增框
        assert len(w.regions) == 1
        ks = main.norm_keys(w.regions[0])
        assert len(ks) == 1 and ks[0][0] == 0.0, "新框默认 1 个关键帧@0"
        # 拖到第 10 帧并移动红框 → 自动打第 2 个关键帧
        w._on_rect_transformed(w._sel, QRect(100, 100, 160, 40))
        ks = main.norm_keys(w.regions[0])
        assert len(ks) == 2, f"自动关键帧失败: {ks}"
        assert abs(ks[1][0] - w._cur_t) < 1e-4, "关键帧时间=当前帧时间"
        assert tuple(ks[1][1]) == (100.0, 100.0, 160.0, 40.0)
        print("auto key ok:", ks)
        # 关键帧提示（首末都显示）
        ghosts = w._ghost_items()
        assert len(ghosts) == 2, "首末关键帧虚线提示"
        # 再拖到第 20 帧打第 3 个关键帧
        w._seek_frame(20)
        w._on_rect_transformed(w._sel, QRect(300, 150, 160, 40))
        ks = main.norm_keys(w.regions[0])
        assert len(ks) == 3
        # 中点插值检查
        mid = main.rect_at(w.regions[0], (ks[0][0] + ks[1][0]) / 2)
        assert abs(mid[0] - (461 + 100) / 2) < 2, f"插值错误: {mid}"
        print("3 keys ok:", [(round(t, 3), r) for t, r in ks])
        # 删除此时关键帧（第 20 帧那个）
        w._del_key_here()
        ks = main.norm_keys(w.regions[0])
        assert len(ks) == 2, "删除当前帧关键帧"
        # 此处结束生效
        w._seek_frame(15)
        w._key_end_here()
        ks = main.norm_keys(w.regions[0])
        assert abs(ks[-1][0] - w._cur_t) < 1e-4 and len(ks) == 3
        # 设为静止全程
        w._make_static()
        ks = main.norm_keys(w.regions[0])
        assert len(ks) == 1 and ks[0][0] == 0.0
        print("key edit ok")

        # --- 方案保存（keys 格式） ---
        w._save_scheme()
        assert w.schemes[0]["regions"][0]["keys"], "方案保存为 keys 格式"
        print("scheme keys:", w.schemes[0]["regions"][0]["keys"])

        # --- 播放 / 暂停 / 停止 ---
        before = w._cur_f

        def check_played():
            try:
                print("played to frame:", w._cur_f)
                assert w._playing and w._cur_f > before, "播放未推进"
                w._pause()
                assert not w._playing
                w._stop()
                assert w._cur_f == 0 and not w._playing
                print("play/pause/stop ok")
            except Exception:
                import traceback
                traceback.print_exc()
            finally:
                app.quit()

        w._play()
        QTimer.singleShot(600, check_played)

    QTimer.singleShot(200, flow)
    return orig_exec()


QApplication.exec_ = staticmethod(patched_exec)
sys.exit = lambda *a: None
main.main()
print("PLAYBACK TEST DONE")
