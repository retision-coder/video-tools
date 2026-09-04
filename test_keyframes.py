# -*- coding: utf-8 -*-
"""关键帧模型单元测试：norm_keys / rect_at / region_active / scale_regions。"""
import main


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print("ok:", msg)


# --- 旧格式迁移 ---
old_static = {"mode": "inpaint", "r0": (10, 20, 100, 40), "t0": 0.0,
              "t1": None, "r1": None}
ks = main.norm_keys(old_static)
check(len(ks) == 1 and ks[0][0] == 0.0, "旧静态框 → 1 个关键帧")
check(main.region_active(old_static, 0) and main.region_active(old_static, 999),
      "单关键帧全程生效")

old_range = {"mode": "blur", "r0": (10, 20, 100, 40), "t0": 2.0,
             "t1": 5.0, "r1": None}
check(not main.region_active(old_range, 1.0)
      and main.region_active(old_range, 3.0)
      and not main.region_active(old_range, 6.0), "旧时间范围 2s~5s 生效区间正确")

old_move = {"mode": "inpaint", "r0": (0, 0, 100, 40), "t0": 0.0,
            "t1": 4.0, "r1": (400, 0, 100, 40)}
x, y, w, h = main.rect_at(old_move, 2.0)
check(abs(x - 200) < 1e-6, "旧移动框中点插值 x=200")

# --- 新关键帧模型 ---
reg = {"mode": "inpaint",
       "keys": [(0.0, (0, 0, 100, 40)), (1.0, (100, 0, 100, 40)),
                (3.0, (100, 200, 100, 40))]}
check(not main.region_active(reg, -0.1) and main.region_active(reg, 0.0)
      and main.region_active(reg, 3.0) and not main.region_active(reg, 3.1),
      "关键帧生效区间 [首帧, 末帧]")
x, y, w, h = main.rect_at(reg, 0.5)
check(abs(x - 50) < 1e-6 and abs(y) < 1e-6, "K1→K2 中点 (50,0)")
x, y, w, h = main.rect_at(reg, 2.0)
check(abs(x - 100) < 1e-6 and abs(y - 100) < 1e-6, "K2→K3 中点 (100,100)")
check(main.rect_at(reg, 99) == (100.0, 200.0, 100.0, 40.0),
      "末关键帧后 rect 停在末位置（active 判定为不生效）")

# 帧级精度：t 值不取整
reg_f = {"mode": "blur", "keys": [(1 / 30, (5, 5, 10, 10))]}
check(main.region_active(reg_f, 1 / 30) and not main.region_active(reg_f, 0.0),
      "帧级关键帧时间（1/30 秒）精确生效")

# --- scale_regions 新格式 ---
scaled = main.scale_regions(
    [{"mode": "inpaint", "keys": [[0.0, [100, 50, 200, 60]],
                                  [2.0, [300, 150, 200, 60]]]}],
    640, 360, 1280, 720)
check(scaled[0]["keys"][0][1] == [200, 100, 400, 120]
      and scaled[0]["keys"][1][1] == [600, 300, 400, 120],
      "关键帧分辨率等比缩放")

# 旧格式缩放仍兼容
scaled_old = main.scale_regions(
    [{"mode": "inpaint", "r0": (100, 50, 200, 60),
      "t0": 0, "t1": None, "r1": (300, 150, 200, 60)}],
    640, 360, 1280, 720)
check(scaled_old[0]["r0"] == (200, 100, 400, 120)
      and scaled_old[0]["r1"] == (600, 300, 400, 120), "旧格式缩放兼容")

print("KEYFRAME TEST DONE")
