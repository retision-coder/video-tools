# -*- coding: utf-8 -*-
"""
视频水印擦除工具
----------------
- 支持常见本地视频格式（mp4/avi/mkv/mov/flv/wmv/ts/m4v/webm/mpg/3gp 等）
- 预览画面 + 时间轴拖动定位
- 支持【多个水印区域】：拖框添加、四角拖拽调大小、按住移动、右键/Delete 删除
- 每个区域可单独设置：擦除样式、生效时间范围、移动结束位置（线性跟随移动水印）
- 【方案】：当前框选配置可保存为"方案1/方案2…"，同水印视频一键套用；
  方案按保存时的分辨率记录，套用到不同分辨率视频时自动等比缩放
- 【批量处理】：一次导入多个视频或整个文件夹，套用当前框选，统一导出到指定目录
- 擦除样式：智能修复（OpenCV NS inpaint + 掩码外扩 + 接缝羽化）/ 高斯模糊 / 马赛克
- 处理引擎：OpenCV 逐帧处理 + ffmpeg 编码（imageio-ffmpeg 自带，pip 安装即可）

CLI 模式（--rect 可重复，字段：x,y,w,h[,mode[,t0,t1[,x1,y1]]]）：
    WatermarkEraser.exe --cli --input a.mp4 --output out.mp4 ^
        --rect 10,10,200,60 --rect 15,295,210,55,blur,0,8,415,295
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.request

APP_NAME = "视频水印擦除工具"
APP_VERSION = "1.4.2"

# 在线升级：GitHub Releases
UPDATE_REPO = "retision-coder/video-tools"
UPDATE_API = (os.environ.get("WATERMARK_UPDATE_API")
              or f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest")

VIDEO_EXTS = (".mp4", ".avi", ".mkv", ".mov", ".flv", ".wmv", ".ts",
              ".m4v", ".webm", ".mpg", ".mpeg", ".3gp", ".rmvb", ".rm")
VIDEO_FILTER = ("视频文件 (" + " ".join("*" + e for e in VIDEO_EXTS) +
                ");;所有文件 (*)")

# 水印样式 -> 处理模式
STYLE_MODES = [
    ("文字 / Logo / 台标（智能修复，与周边融合）", "inpaint"),
    ("AI 精修（LaMa 大模型，最干净，较慢）", "inpaint_ai"),
    ("半透明水印（高斯模糊）", "blur"),
    ("复杂背景水印（马赛克）", "mosaic"),
]
MODE_NAMES = {"inpaint": "智能修复", "inpaint_ai": "AI精修",
              "blur": "高斯模糊", "mosaic": "马赛克"}
MODE_ALIASES = {"delogo": "inpaint"}  # 兼容旧命令行

PRESETS = [
    "自定义（在画面上拖框）",
    "左上角",
    "右上角",
    "左下角",
    "右下角",
    "顶部居中",
    "底部居中",
    "正中央",
]

_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


# ---------------------------------------------------------------------------
# 路径 / 方案持久化
# ---------------------------------------------------------------------------

def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def config_dir():
    """用户配置目录（%APPDATA%\\视频水印擦除工具），保证安装到
    Program Files 后仍可写，且在线升级覆盖 exe 时方案不丢失。"""
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        d = os.path.join(base, APP_NAME)
    else:
        d = os.path.join(os.path.expanduser("~"), ".watermark_eraser")
    os.makedirs(d, exist_ok=True)
    return d


def _migrate_schemes():
    """把旧版本保存在 exe 同目录的方案文件迁移到配置目录。"""
    new = os.path.join(config_dir(), "watermark_schemes.json")
    old = os.path.join(app_dir(), "watermark_schemes.json")
    try:
        if os.path.isfile(old) and not os.path.isfile(new):
            shutil.copy2(old, new)
    except Exception:
        pass
    return new


SCHEMES_PATH = _migrate_schemes()


def load_schemes():
    try:
        with open(SCHEMES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_schemes(schemes):
    with open(SCHEMES_PATH, "w", encoding="utf-8") as f:
        json.dump(schemes, f, ensure_ascii=False, indent=2)


def scale_regions(regions, rw, rh, tw, th):
    """把区域从参考分辨率 (rw,rh) 等比缩放到目标分辨率 (tw,th)。"""
    out = []
    if not rw or not rh or not tw or not th:
        return [dict(r) for r in regions]
    sx, sy = tw / rw, th / rh
    for r in regions:
        nr = dict(r)
        nr["r0"] = tuple(int(round(a * b))
                         for a, b in zip(r["r0"], (sx, sy, sx, sy)))
        if r.get("r1"):
            nr["r1"] = tuple(int(round(a * b))
                             for a, b in zip(r["r1"], (sx, sy, sx, sy)))
        out.append(nr)
    return out


# ---------------------------------------------------------------------------
# ffmpeg 工具函数
# ---------------------------------------------------------------------------

def get_ffmpeg():
    exe = os.environ.get("IMAGEIO_FFMPEG_EXE")
    if exe and os.path.isfile(exe):
        return exe
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.isfile(exe):
            return exe
    except Exception:
        pass
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.argv[0])))
    for cand in (os.path.join(base, "ffmpeg.exe"), os.path.join(base, "ffmpeg")):
        if os.path.isfile(cand):
            return cand
    raise RuntimeError("未找到 ffmpeg。请 pip install imageio-ffmpeg 或安装 ffmpeg。")


def get_duration(ffmpeg, path):
    try:
        p = subprocess.run(
            [ffmpeg, "-hide_banner", "-i", path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=_CREATE_NO_WINDOW, timeout=30,
        )
        m = re.search(rb"Duration:\s*(\d+):(\d+):([\d.]+)", p.stderr)
        if m:
            return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    except Exception:
        pass
    return 0.0


def grab_frame_bytes(ffmpeg, path, t_sec):
    """用 ffmpeg 抽取 t 秒处的一帧 PNG，返回 bytes。"""
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error",
        "-ss", f"{max(0.0, t_sec):.3f}", "-i", path,
        "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-",
    ]
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           creationflags=_CREATE_NO_WINDOW, timeout=60)
        return p.stdout or b""
    except Exception:
        return b""


def probe_size(path):
    """读取视频分辨率，失败返回 (0, 0)。"""
    import cv2
    try:
        cap = cv2.VideoCapture(path)
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap.release()
        return w, h
    except Exception:
        return 0, 0


# ---------------------------------------------------------------------------
# 区域模型与逐帧处理引擎
# ---------------------------------------------------------------------------

def clamp_rect(x, y, w, h, vw, vh):
    w = max(4, min(int(round(w)), vw))
    h = max(4, min(int(round(h)), vh))
    x = max(0, min(int(round(x)), vw - w))
    y = max(0, min(int(round(y)), vh - h))
    return x, y, w, h


def region_active(reg, t):
    t0 = reg.get("t0", 0.0) or 0.0
    t1 = reg.get("t1")
    return t >= t0 and (t1 is None or t <= t1)


def rect_at(reg, t):
    """返回 t 时刻区域的 (x, y, w, h) 浮点值；支持线性移动。"""
    x, y, w, h = reg["r0"]
    r1 = reg.get("r1")
    t0 = reg.get("t0", 0.0) or 0.0
    t1 = reg.get("t1")
    if r1 and t1 is not None and t1 > t0:
        k = min(1.0, max(0.0, (t - t0) / (t1 - t0)))
        return (x + (r1[0] - x) * k, y + (r1[1] - y) * k,
                w + (r1[2] - w) * k, h + (r1[3] - h) * k)
    return float(x), float(y), float(w), float(h)


def _apply_region(frame, mode, x, y, w, h):
    import cv2
    import numpy as np
    fh, fw = frame.shape[:2]
    x, y, w, h = clamp_rect(x, y, w, h, fw, fh)
    if mode == "inpaint":
        pad = 20
        x0, y0 = max(0, x - pad), max(0, y - pad)
        x1, y1 = min(fw, x + w + pad), min(fh, y + h + pad)
        roi = frame[y0:y1, x0:x1]
        mask = np.zeros(roi.shape[:2], np.uint8)
        mask[y - y0:y - y0 + h, x - x0:x - x0 + w] = 255
        # 掩码外扩，吃掉水印边缘残留
        mask = cv2.dilate(mask, np.ones((9, 9), np.uint8), iterations=2)
        # Navier-Stokes 修复：大块区域融合比 Telea 更平滑
        res = cv2.inpaint(roi, mask, 7, cv2.INPAINT_NS)
        # 接缝羽化：软掩码混合，消除修复区与周边的边界痕
        soft = cv2.GaussianBlur(mask.astype(np.float32) / 255.0, (0, 0), 4)[..., None]
        frame[y0:y1, x0:x1] = (
            res.astype(np.float32) * soft
            + roi.astype(np.float32) * (1 - soft)
        ).astype(np.uint8)
    elif mode == "blur":
        roi = frame[y:y + h, x:x + w]
        frame[y:y + h, x:x + w] = cv2.GaussianBlur(roi, (0, 0), 28)
    elif mode == "mosaic":
        roi = frame[y:y + h, x:x + w]
        small = cv2.resize(roi, (max(2, w // 14), max(2, h // 14)),
                           interpolation=cv2.INTER_LINEAR)
        frame[y:y + h, x:x + w] = cv2.resize(small, (w, h),
                                             interpolation=cv2.INTER_NEAREST)
    return frame


def process_video(ffmpeg, inp, outp, regions, progress_cb=None, cancel=None):
    """
    逐帧处理视频。regions: [{'mode','r0','t0','t1','r1'}, ...]
    返回 (ok, message)。
    """
    import cv2
    if not regions:
        return False, "没有有效的水印区域"
    cap = cv2.VideoCapture(inp)
    if not cap.isOpened():
        return False, "无法打开视频文件"
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    if fps <= 1:
        fps = 25.0
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0

    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "bgr24",
        "-s", f"{W}x{H}", "-r", f"{fps:.6f}", "-i", "-",
        "-i", inp,
        "-map", "0:v:0", "-map", "1:a:0?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-pix_fmt", "yuv420p", "-c:a", "copy",
        "-movflags", "+faststart",
        outp,
    ]
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                creationflags=_CREATE_NO_WINDOW)
    except Exception as e:
        cap.release()
        return False, f"无法启动 ffmpeg：{e}"

    stderr_tail = []

    def read_stderr():
        try:
            for line in proc.stderr:
                stderr_tail.append(line.decode("utf-8", "replace").rstrip()
                                   if isinstance(line, bytes) else line.rstrip())
                del stderr_tail[:-20]
        except Exception:
            pass

    threading.Thread(target=read_stderr, daemon=True).start()

    idx = 0
    err = None
    try:
        while True:
            if cancel is not None and cancel.is_set():
                err = "已取消"
                break
            ok, frame = cap.read()
            if not ok:
                break
            t = idx / fps
            for reg in regions:
                if region_active(reg, t):
                    frame = _apply_region(frame, reg["mode"],
                                          *rect_at(reg, t))
            proc.stdin.write(frame.tobytes())
            idx += 1
            if progress_cb and total > 0 and idx % 5 == 0:
                progress_cb(min(99, int(idx / total * 100)))
    except (BrokenPipeError, OSError):
        err = err or "编码管道中断"
    except Exception as e:
        err = f"处理中断：{e}"
    finally:
        cap.release()
        try:
            proc.stdin.close()
        except Exception:
            pass

    if err == "已取消":
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        return False, err

    proc.wait()
    if err:
        try:
            proc.kill()
        except Exception:
            pass
        return False, err
    if proc.returncode == 0 and os.path.isfile(outp) and os.path.getsize(outp) > 0:
        if progress_cb:
            progress_cb(100)
        return True, outp
    tail = "\n".join(stderr_tail[-8:])
    return False, f"ffmpeg 编码失败（退出码 {proc.returncode}）\n{tail}"


def run_batch(ffmpeg, files, outdir, regions, ref_size,
              file_cb=None, overall_cb=None, cancel=None):
    """
    批量处理：对每个文件按 ref_size 等比缩放区域后处理，输出到 outdir。
    file_cb(filename, ok, msg)；overall_cb(percent)。
    返回 [(path, ok, msg), ...]
    """
    os.makedirs(outdir, exist_ok=True)
    results = []
    n = len(files)
    rw, rh = ref_size
    for i, path in enumerate(files):
        if cancel is not None and cancel.is_set():
            results.append((path, False, "已取消"))
            break
        stem = os.path.splitext(os.path.basename(path))[0]
        outp = os.path.join(outdir, stem + "_去水印.mp4")
        tw, th = probe_size(path)
        if not tw:
            results.append((path, False, "无法读取视频，已跳过"))
            if file_cb:
                file_cb(path, False, "无法读取视频，已跳过")
            continue
        scaled = scale_regions(regions, rw, rh, tw, th)

        def cb(p, i=i):
            if overall_cb:
                overall_cb(min(99, int((i + p / 100.0) / n * 100)))

        ok, msg = process_video_auto(ffmpeg, path, outp, scaled,
                                     progress_cb=cb, cancel=cancel)
        results.append((path, ok, msg))
        if file_cb:
            file_cb(path, ok, msg)
    if overall_cb:
        overall_cb(100)
    return results


# ---------------------------------------------------------------------------
# 在线升级（GitHub Releases）
# ---------------------------------------------------------------------------

def parse_version(s):
    """'v1.4.0' -> (1, 4, 0)"""
    m = re.findall(r"\d+", s or "")
    if not m:
        return (0, 0, 0)
    return tuple(int(x) for x in (m + ["0", "0", "0"])[:3])


def _quote_url(url):
    """把 URL 路径里的非 ASCII 字符（如中文文件名）百分号编码。"""
    from urllib.parse import quote, urlsplit, urlunsplit
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, quote(p.path), p.query, p.fragment))


def fetch_latest_release(timeout=15):
    """获取最新 Release 信息 dict，失败抛异常。"""
    req = urllib.request.Request(
        _quote_url(UPDATE_API),
        headers={"User-Agent": "VideoWatermarkEraser-Updater",
                 "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def pick_asset(release):
    """从 Release 里挑安装包资产（优先名字含 Setup 的 exe）。"""
    assets = release.get("assets") or []
    exes = [a for a in assets
            if str(a.get("name", "")).lower().endswith(".exe")
            and a.get("browser_download_url")]
    for a in exes:
        if "setup" in a["name"].lower():
            return a
    return exes[0] if exes else None


def download_file(url, dst, progress_cb=None, cancel=None):
    req = urllib.request.Request(
        _quote_url(url), headers={"User-Agent": "VideoWatermarkEraser-Updater"})
    with urllib.request.urlopen(req, timeout=60) as r, open(dst, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            if cancel is not None and cancel.is_set():
                raise RuntimeError("已取消")
            chunk = r.read(65536)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress_cb and total:
                progress_cb(min(99, int(done / total * 100)))
    if progress_cb:
        progress_cb(100)
    return dst


def build_update_bat(setup_path, instdir, app_exe):
    """生成升级脚本：等本程序退出 → 静默覆盖安装 → 启动新版 → 清理。"""
    bat = os.path.join(tempfile.gettempdir(), "videotools_update.bat")
    lines = [
        "@echo off",
        "ping 127.0.0.1 -n 4 >nul",
        f'"{setup_path}" /S /D="{instdir}"',
        f'start "" "{app_exe}"',
        f'del /f /q "{setup_path}"',
        '(goto) 2>nul & del "%~f0"',
    ]
    with open(bat, "w", encoding="gbk") as f:
        f.write("\r\n".join(lines) + "\r\n")
    return bat


# ---------------------------------------------------------------------------
# AI 精修组件（LaMa，按需下载，离线运行，无需 token）
# ---------------------------------------------------------------------------

UV_URL = ("https://github.com/astral-sh/uv/releases/latest/download/"
          "uv-x86_64-pc-windows-msvc.zip")


def ai_component_dir():
    return os.path.join(config_dir(), "ai")


def ai_python():
    p = os.path.join(ai_component_dir(), "venv", "Scripts", "python.exe")
    return p if os.path.isfile(p) else None


def ai_ready():
    p = ai_python()
    if not p:
        return False
    try:
        r = subprocess.run(
            [p, "-c", "import simple_lama_inpainting, cv2, PIL"],
            capture_output=True, timeout=120, creationflags=_CREATE_NO_WINDOW)
        return r.returncode == 0
    except Exception:
        return False


def ensure_ai_component(log=print, cancel=None):
    """按需下载并安装 AI 组件：uv → 独立 Python → torch + LaMa。
    返回 AI 环境的 python 路径；失败抛异常。"""
    import zipfile
    adir = ai_component_dir()
    os.makedirs(adir, exist_ok=True)
    uv = os.path.join(adir, "uv.exe")
    if cancel is not None and cancel.is_set():
        raise RuntimeError("已取消")
    if not os.path.isfile(uv):
        log("① 下载 uv 安装工具（约 40MB）…")
        tmp = os.path.join(adir, "uv.zip")
        download_file(UV_URL, tmp)
        with zipfile.ZipFile(tmp) as z:
            for n in z.namelist():
                if n.replace("\\", "/").endswith("uv.exe"):
                    src = z.extract(n, adir)
                    shutil.move(src, uv)
                    break
        os.remove(tmp)
        # 清理 zip 里的中间目录
        for d in os.listdir(adir):
            dp = os.path.join(adir, d)
            if os.path.isdir(dp) and d != "venv":
                shutil.rmtree(dp, ignore_errors=True)
    vdir = os.path.join(adir, "venv")
    py = os.path.join(vdir, "Scripts", "python.exe")
    if cancel is not None and cancel.is_set():
        raise RuntimeError("已取消")
    if not os.path.isfile(py):
        log("② 安装独立 Python 运行环境…")
        r = subprocess.run([uv, "venv", "--python", "3.11", vdir],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           creationflags=_CREATE_NO_WINDOW)
        if r.returncode != 0 or not os.path.isfile(py):
            raise RuntimeError("Python 环境安装失败：" + (r.stderr or "")[-300:])
    if cancel is not None and cancel.is_set():
        raise RuntimeError("已取消")
    if not ai_ready():
        log("③ 下载 AI 组件（PyTorch + LaMa，约 1GB，请耐心等候）…")
        pkgs = ["simple-lama-inpainting", "opencv-contrib-python-headless"]
        r = subprocess.run(
            [uv, "pip", "install", "--python", py] + pkgs,
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            creationflags=_CREATE_NO_WINDOW)
        if r.returncode != 0:
            # 默认 PyPI 失败/过慢时回退到清华镜像（国内网络更快）
            log("默认源较慢，切换到国内镜像重试…")
            r = subprocess.run(
                [uv, "pip", "install", "--python", py,
                 "--index-url", "https://pypi.tuna.tsinghua.edu.cn/simple"] + pkgs,
                capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                creationflags=_CREATE_NO_WINDOW)
        if r.returncode != 0:
            raise RuntimeError("AI 组件安装失败：" + (r.stderr or "")[-300:])
    if not ai_ready():
        raise RuntimeError("AI 组件校验失败，请重试")
    log("AI 组件就绪")
    return py


def engine_script():
    if getattr(sys, "frozen", False):
        src = os.path.join(sys._MEIPASS, "ai_engine.py")
        # 不能直接在 _MEIPASS 里运行：脚本所在目录会成为子进程 sys.path[0]，
        # _MEIPASS 里打包版自带的 3.12 版 _ctypes.pyd 会污染 AI 环境导致
        # "python312.dll conflicts" 错误。复制到干净目录再运行。
        dst = os.path.join(config_dir(), "ai_engine_runtime.py")
        try:
            if (not os.path.isfile(dst)
                    or os.path.getsize(dst) != os.path.getsize(src)):
                shutil.copy2(src, dst)
        except Exception:
            return src
        return dst
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "ai_engine.py")


def process_video_ai(ffmpeg, inp, outp, regions,
                     progress_cb=None, cancel=None, status_cb=None):
    """用 AI 组件环境逐帧处理（含 LaMa 精修）。返回 (ok, msg)。"""
    py = ai_python()
    if not py:
        return False, "AI 组件未安装"
    fd, rj = tempfile.mkstemp(suffix=".json", prefix="wme_regions_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(regions, f)
        cmd = [py, engine_script(), "--input", inp, "--output", outp,
               "--regions", rj, "--ffmpeg", ffmpeg]
        # PyInstaller onefile 会把 _MEIPASS 注入 PATH，子进程（AI venv 的
        # python 3.11）会因此误加载打包版自带的 python312.dll 等 DLL 而崩溃。
        # 直接给子进程换成最小安全 PATH。
        env = os.environ.copy()
        if getattr(sys, "frozen", False):
            sysroot = env.get("SystemRoot", r"C:\Windows")
            env["PATH"] = os.pathsep.join([
                os.path.dirname(py),
                os.path.join(sysroot, "System32"), sysroot])
            env.pop("PYTHONPATH", None)
            env.pop("PYTHONHOME", None)
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                env=env, creationflags=_CREATE_NO_WINDOW)
        except Exception as e:
            return False, f"无法启动 AI 引擎：{e}"
        err_lines = []
        try:
            for line in proc.stdout:
                line = line.strip()
                if line == "MODEL_LOADING" and status_cb:
                    status_cb("正在加载 LaMa 模型（首次会自动下载约 200MB）…")
                elif line == "MODEL_READY" and status_cb:
                    status_cb("模型就绪，逐帧 AI 修复中（较慢属正常）…")
                elif line.startswith("PROGRESS ") and progress_cb:
                    _, i, n = line.split()
                    i, n = int(i), int(n)
                    if n > 0:
                        progress_cb(min(99, int(i / n * 100)))
                elif line.startswith("ERROR"):
                    err_lines.append(line)
                elif line and not line.startswith("PROGRESS"):
                    err_lines.append(line)  # 保留引擎其它输出便于诊断
                    del err_lines[:-12]
                if cancel is not None and cancel.is_set():
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except Exception:
                        proc.kill()
                    return False, "已取消"
            proc.wait()
        except Exception as e:
            try:
                proc.kill()
            except Exception:
                pass
            return False, f"AI 处理中断：{e}"
        if proc.returncode == 0 and os.path.isfile(outp) \
                and os.path.getsize(outp) > 0:
            if progress_cb:
                progress_cb(100)
            return True, outp
        return False, "AI 处理失败\n" + "\n".join(err_lines[-5:])
    finally:
        try:
            os.remove(rj)
        except OSError:
            pass


def has_ai_region(regions):
    return any(r.get("mode") == "inpaint_ai" for r in regions)


def process_video_auto(ffmpeg, inp, outp, regions,
                       progress_cb=None, cancel=None, status_cb=None):
    """按区域模式自动路由：含 AI 精修时走 LaMa 引擎，否则走本地快速算法。"""
    if has_ai_region(regions):
        return process_video_ai(ffmpeg, inp, outp, regions,
                                progress_cb=progress_cb, cancel=cancel,
                                status_cb=status_cb)
    return process_video(ffmpeg, inp, outp, regions,
                         progress_cb=progress_cb, cancel=cancel)


# ---------------------------------------------------------------------------
# CLI 模式
# ---------------------------------------------------------------------------

def cli_main(args):
    ffmpeg = get_ffmpeg()
    regions = []
    for item in args.rect:
        parts = [p.strip() for p in item.split(",")]
        if len(parts) not in (4, 5, 7, 9):
            print(f"错误：--rect 格式 x,y,w,h[,mode[,t0,t1[,x1,y1]]]，收到：{item}")
            return 2
        try:
            x, y, w, h = [int(v) for v in parts[:4]]
        except ValueError:
            print(f"错误：--rect 坐标必须是整数：{item}")
            return 2
        mode = parts[4] if len(parts) >= 5 else args.mode
        mode = MODE_ALIASES.get(mode, mode)
        if mode not in MODE_NAMES:
            print(f"错误：未知模式 {mode}（可选 inpaint/blur/mosaic/inpaint_ai）")
            return 2
        t0 = float(parts[5]) if len(parts) >= 7 else 0.0
        t1 = float(parts[6]) if len(parts) >= 7 else None
        r1 = None
        if len(parts) == 9:
            r1 = (int(parts[7]), int(parts[8]), w, h)
        regions.append({"mode": mode, "r0": (x, y, w, h),
                        "t0": t0, "t1": t1, "r1": r1})

    def cb(p):
        print(f"\r进度 {p}%", end="", flush=True)

    if has_ai_region(regions) and not ai_ready():
        print("错误：所选模式包含 AI 精修，但 AI 组件尚未安装。")
        print("请先打开图形界面，在框选样式中选择「AI 精修」并按提示下载组件（约 1GB，仅首次需要）。")
        return 3

    def status_cb(s):
        print(f"\n{s}", flush=True)

    ok, msg = process_video_auto(ffmpeg, args.input, args.output, regions,
                                 progress_cb=cb, status_cb=status_cb)
    print()
    if ok:
        print(f"完成：{msg}")
        return 0
    print(f"失败：{msg}")
    return 1


# ---------------------------------------------------------------------------
# GUI 模式
# ---------------------------------------------------------------------------

def gui_main():
    from PyQt5.QtCore import (QPoint, QRect, QSize, Qt, QThread, QTimer,
                              pyqtSignal)
    from PyQt5.QtGui import QImage, QPainter, QPen, QColor
    from PyQt5.QtWidgets import (QApplication, QComboBox, QDialog, QFileDialog,
                                 QGroupBox, QHBoxLayout, QInputDialog, QLabel,
                                 QLineEdit, QListWidget, QMainWindow,
                                 QMessageBox, QProgressBar, QProgressDialog,
                                 QPushButton,
                                 QSlider, QSplitter, QVBoxLayout, QWidget)

    HANDLE = 8  # 角手柄命中半径（控件像素）

    class PreviewLabel(QLabel):
        """视频帧预览 + 多框交互（拖框/移动/四角缩放/删除）。
        几何数据由主窗口下发：items = [(QRect, active:bool), ...]"""
        rectDrawn = pyqtSignal(QRect)
        rectTransformed = pyqtSignal(int, QRect)
        selectionChanged = pyqtSignal(int)
        deleteRequested = pyqtSignal(int)

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setMinimumSize(QSize(480, 270))
            self.setAlignment(Qt.AlignCenter)
            self.setFocusPolicy(Qt.StrongFocus)
            self.setMouseTracking(True)
            self.setStyleSheet("background:#111;color:#888;")
            self.setText("请先选择视频文件")
            self._img = None
            self._items = []
            self._sel = -1
            self._op = None    # ('draw',) ('move',i) ('resize',i,corner)
            self._anchor = QPoint()
            self._grab_offset = QPoint()
            self._draft = None

        def set_frame(self, qimg):
            self._img = qimg
            self.update()

        def set_items(self, items, sel):
            self._items = [(QRect(r), a) for r, a in items]
            self._sel = sel
            self.update()

        def _transform(self):
            if self._img is None or self._img.isNull():
                return 1.0, 0, 0
            vw, vh = self._img.width(), self._img.height()
            lw, lh = max(1, self.width()), max(1, self.height())
            scale = min(lw / vw, lh / vh)
            return scale, (lw - vw * scale) / 2.0, (lh - vh * scale) / 2.0

        def _to_video(self, pt):
            scale, ox, oy = self._transform()
            vx = (pt.x() - ox) / scale
            vy = (pt.y() - oy) / scale
            vw, vh = self._img.width(), self._img.height()
            return QPoint(int(max(0, min(vw - 1, round(vx)))),
                          int(max(0, min(vh - 1, round(vy)))))

        def _to_widget_pt(self, vp):
            scale, ox, oy = self._transform()
            return QPoint(int(ox + vp.x() * scale), int(oy + vp.y() * scale))

        def _hit(self, pt):
            for i in range(len(self._items) - 1, -1, -1):
                if self._items[i][0].adjusted(-4, -4, 4, 4).contains(pt):
                    return i
            return -1

        def _hit_corner(self, i, widget_pt):
            r = self._items[i][0]
            corners = [r.topLeft(), r.topRight(), r.bottomLeft(), r.bottomRight()]
            for c, vp in enumerate(corners):
                wp = self._to_widget_pt(vp)
                if abs(widget_pt.x() - wp.x()) <= HANDLE and \
                   abs(widget_pt.y() - wp.y()) <= HANDLE:
                    return c
            return -1

        def paintEvent(self, ev):
            super().paintEvent(ev)
            if self._img is None or self._img.isNull():
                return
            p = QPainter(self)
            scale, ox, oy = self._transform()
            target = QRect(int(ox), int(oy),
                           int(self._img.width() * scale),
                           int(self._img.height() * scale))
            p.drawImage(target, self._img)
            for i, (r, active) in enumerate(self._items):
                dr = QRect(int(ox + r.x() * scale), int(oy + r.y() * scale),
                           max(2, int(r.width() * scale)),
                           max(2, int(r.height() * scale)))
                if not active:
                    pen = QPen(QColor(150, 150, 150), 1, Qt.DashLine)
                    brush = QColor(150, 150, 150, 20)
                elif i == self._sel:
                    pen = QPen(QColor(255, 60, 60), 2)
                    brush = QColor(255, 60, 60, 40)
                else:
                    pen = QPen(QColor(255, 170, 0), 2, Qt.DashLine)
                    brush = QColor(255, 170, 0, 30)
                p.setPen(pen)
                p.setBrush(brush)
                p.drawRect(dr)
                if active and i == self._sel:
                    p.setBrush(QColor(255, 60, 60))
                    for vp in (r.topLeft(), r.topRight(),
                               r.bottomLeft(), r.bottomRight()):
                        wp = self._to_widget_pt(vp)
                        p.drawRect(wp.x() - 3, wp.y() - 3, 6, 6)
                p.setPen(QPen(QColor(255, 255, 255), 1))
                p.drawText(dr.left() + 3, dr.top() + 14, f"#{i + 1}")
            if self._op and self._op[0] == "draw" and self._draft:
                r = self._draft
                dr = QRect(int(ox + r.x() * scale), int(oy + r.y() * scale),
                           max(2, int(r.width() * scale)),
                           max(2, int(r.height() * scale)))
                p.setPen(QPen(QColor(255, 60, 60), 2, Qt.DashLine))
                p.setBrush(QColor(255, 60, 60, 40))
                p.drawRect(dr)
            p.end()

        def mousePressEvent(self, ev):
            if self._img is None:
                return
            self.setFocus()
            if ev.button() == Qt.RightButton:
                i = self._hit(self._to_video(ev.pos()))
                if i >= 0:
                    self.deleteRequested.emit(i)
                return
            if ev.button() != Qt.LeftButton:
                return
            pt = self._to_video(ev.pos())
            i = self._hit(pt)
            if i >= 0:
                c = self._hit_corner(i, ev.pos())
                if c >= 0:
                    self._op = ("resize", i, c)
                else:
                    self._op = ("move", i)
                    self._grab_offset = pt - self._items[i][0].topLeft()
                if self._sel != i:
                    self._sel = i
                    self.selectionChanged.emit(i)
                self.update()
            else:
                self._op = ("draw",)
                self._anchor = pt
                self._draft = QRect(pt, QSize(2, 2))

        def mouseMoveEvent(self, ev):
            if self._img is None:
                return
            if self._op is None:
                i = self._hit(self._to_video(ev.pos()))
                if i >= 0 and self._hit_corner(i, ev.pos()) >= 0:
                    self.setCursor(Qt.SizeFDiagCursor)
                elif i >= 0:
                    self.setCursor(Qt.SizeAllCursor)
                else:
                    self.unsetCursor()
                return
            pt = self._to_video(ev.pos())
            vw, vh = self._img.width(), self._img.height()
            if self._op[0] == "draw":
                self._draft = QRect(self._anchor, pt).normalized()
                self.update()
            elif self._op[0] == "move":
                i = self._op[1]
                r = QRect(self._items[i][0])
                tl = pt - self._grab_offset
                tl.setX(max(0, min(vw - r.width(), tl.x())))
                tl.setY(max(0, min(vh - r.height(), tl.y())))
                r.moveTopLeft(tl)
                self._items[i] = (r, self._items[i][1])
                self.rectTransformed.emit(i, r)
                self.update()
            elif self._op[0] == "resize":
                i, c = self._op[1], self._op[2]
                r0 = self._items[i][0]
                x0, y0, x1, y1 = r0.left(), r0.top(), r0.right(), r0.bottom()
                if c in (0, 3):
                    x0 = pt.x()
                if c in (1, 2):
                    x1 = pt.x()
                if c in (0, 1):
                    y0 = pt.y()
                if c in (2, 3):
                    y1 = pt.y()
                x0 = max(0, min(x0, vw - 8))
                y0 = max(0, min(y0, vh - 8))
                x1 = max(x0 + 7, min(x1, vw - 1))
                y1 = max(y0 + 7, min(y1, vh - 1))
                r = QRect(QPoint(x0, y0), QPoint(x1, y1)).normalized()
                self._items[i] = (r, self._items[i][1])
                self.rectTransformed.emit(i, r)
                self.update()

        def mouseReleaseEvent(self, ev):
            if self._img is None or self._op is None:
                return
            if self._op[0] == "draw" and self._draft:
                if self._draft.width() >= 8 and self._draft.height() >= 8:
                    self.rectDrawn.emit(self._draft.normalized())
                self._draft = None
            self._op = None
            self.unsetCursor()
            self.update()

        def keyPressEvent(self, ev):
            if ev.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self._sel >= 0:
                self.deleteRequested.emit(self._sel)
            else:
                super().keyPressEvent(ev)

    class ProcessThread(QThread):
        progress = pyqtSignal(int)
        status = pyqtSignal(str)
        finished_ = pyqtSignal(bool, str)

        def __init__(self, ffmpeg, inp, outp, regions):
            super().__init__()
            self.args = (ffmpeg, inp, outp, regions)
            self.cancel_ev = threading.Event()

        def run(self):
            ok, msg = process_video_auto(*self.args,
                                         progress_cb=self.progress.emit,
                                         cancel=self.cancel_ev,
                                         status_cb=self.status.emit)
            self.finished_.emit(ok, msg)

    class BatchWorker(QThread):
        overall = pyqtSignal(int)
        file_done = pyqtSignal(str, bool, str)
        finished_ = pyqtSignal(object)

        def __init__(self, ffmpeg, files, outdir, regions, ref_size):
            super().__init__()
            self.args = (ffmpeg, files, outdir, regions, ref_size)
            self.cancel_ev = threading.Event()

        def run(self):
            results = run_batch(
                *self.args,
                file_cb=lambda p, ok, m: self.file_done.emit(p, ok, m),
                overall_cb=self.overall.emit,
                cancel=self.cancel_ev)
            self.finished_.emit(results)

    class BatchDialog(QDialog):
        """批量处理：多视频导入 + 统一输出目录。"""
        def __init__(self, parent, ffmpeg, regions, ref_size):
            super().__init__(parent)
            self.setWindowTitle("批量处理")
            self.resize(640, 520)
            self.ffmpeg = ffmpeg
            self.regions = [dict(r) for r in regions]
            self.ref_size = ref_size
            self.worker = None

            v = QVBoxLayout(self)
            v.addWidget(QLabel(
                f"将应用当前 {len(self.regions)} 个水印框"
                f"（参考分辨率 {ref_size[0]}×{ref_size[1]}，"
                f"不同分辨率的视频会自动等比缩放）"))

            self.list_files = QListWidget()
            v.addWidget(self.list_files, 1)
            brow = QHBoxLayout()
            b1 = QPushButton("添加视频…")
            b1.clicked.connect(self.add_files)
            b2 = QPushButton("添加文件夹…")
            b2.clicked.connect(self.add_folder)
            b3 = QPushButton("移除选中")
            b3.clicked.connect(self.remove_selected)
            b4 = QPushButton("清空")
            b4.clicked.connect(self.list_files.clear)
            for b in (b1, b2, b3, b4):
                brow.addWidget(b)
            v.addLayout(brow)

            drow = QHBoxLayout()
            drow.addWidget(QLabel("输出目录："))
            self.ed_dir = QLineEdit()
            self.ed_dir.setPlaceholderText("选择批量导出的目录")
            bdir = QPushButton("浏览…")
            bdir.clicked.connect(self.choose_dir)
            drow.addWidget(self.ed_dir, 1)
            drow.addWidget(bdir)
            v.addLayout(drow)

            self.lbl_cur = QLabel("就绪")
            v.addWidget(self.lbl_cur)
            self.progress = QProgressBar()
            self.progress.setRange(0, 100)
            v.addWidget(self.progress)
            self.log = QListWidget()
            self.log.setMaximumHeight(120)
            v.addWidget(self.log)

            arow = QHBoxLayout()
            self.btn_start = QPushButton("开始批量处理")
            self.btn_start.setStyleSheet(
                "QPushButton{background:#2d8cf0;color:white;font-weight:bold;"
                "border-radius:4px;padding:6px;}"
                "QPushButton:disabled{background:#9cc4ee;}")
            self.btn_start.clicked.connect(self.start)
            self.btn_cancel = QPushButton("取消")
            self.btn_cancel.setVisible(False)
            self.btn_cancel.clicked.connect(self.cancel)
            btn_close = QPushButton("关闭")
            btn_close.clicked.connect(self.close)
            arow.addWidget(self.btn_start, 1)
            arow.addWidget(self.btn_cancel)
            arow.addWidget(btn_close)
            v.addLayout(arow)

        def add_files(self):
            files, _ = QFileDialog.getOpenFileNames(self, "选择视频（可多选）",
                                                    "", VIDEO_FILTER)
            for f in files:
                self.list_files.addItem(f)

        def add_folder(self):
            d = QFileDialog.getExistingDirectory(self, "选择视频所在文件夹")
            if not d:
                return
            for root, _, names in os.walk(d):
                for nm in sorted(names):
                    if nm.lower().endswith(VIDEO_EXTS):
                        self.list_files.addItem(os.path.join(root, nm))

        def remove_selected(self):
            for item in self.list_files.selectedItems():
                self.list_files.takeItem(self.list_files.row(item))

        def choose_dir(self):
            d = QFileDialog.getExistingDirectory(self, "选择输出目录")
            if d:
                self.ed_dir.setText(d)

        def files(self):
            return [self.list_files.item(i).text()
                    for i in range(self.list_files.count())]

        def start(self):
            files = self.files()
            if not files:
                QMessageBox.warning(self, APP_NAME, "请先添加视频文件")
                return
            if has_ai_region(self.regions) and not ai_ready():
                QMessageBox.warning(
                    self, APP_NAME,
                    "方案中包含「AI 精修」样式，但 AI 组件尚未安装。\n"
                    "请回到主界面单独处理一次并选择 AI 精修，"
                    "按提示完成组件下载（约 1GB，仅首次需要）后再批量处理。")
                return
            outdir = self.ed_dir.text().strip()
            if not outdir:
                # 默认：第一个视频同级的"去水印输出"目录
                outdir = os.path.join(os.path.dirname(os.path.abspath(files[0])),
                                      "去水印输出")
                self.ed_dir.setText(outdir)
            self.worker = BatchWorker(self.ffmpeg, files, outdir,
                                      self.regions, self.ref_size)
            self.worker.overall.connect(self.progress.setValue)
            self.worker.file_done.connect(self._on_file_done)
            self.worker.finished_.connect(self._on_finished)
            self.btn_start.setEnabled(False)
            self.btn_cancel.setVisible(True)
            self.lbl_cur.setText(f"正在处理 0/{len(files)} …")
            self._total = len(files)
            self._done = 0
            self.worker.start()

        def cancel(self):
            if self.worker:
                self.worker.cancel_ev.set()
                self.lbl_cur.setText("正在取消（当前文件处理完后停止）…")

        def _on_file_done(self, path, ok, msg):
            self._done += 1
            self.lbl_cur.setText(f"正在处理 {self._done}/{self._total} …")
            name = os.path.basename(path)
            self.log.addItem(("✅ " if ok else "❌ ") + name +
                             ("" if ok else f"：{msg}"))
            self.log.scrollToBottom()

        def _on_finished(self, results):
            self.btn_start.setEnabled(True)
            self.btn_cancel.setVisible(False)
            ok_n = sum(1 for _, ok, _ in results if ok)
            self.progress.setValue(100)
            self.lbl_cur.setText(f"完成：成功 {ok_n} / 共 {len(results)} 个")
            QMessageBox.information(
                self, APP_NAME,
                f"批量处理结束：成功 {ok_n} 个，失败 {len(results) - ok_n} 个\n"
                f"输出目录：{self.ed_dir.text()}")
            self.worker = None

    class MainWindow(QMainWindow):
        frameGrabbed = pyqtSignal(object, float)
        updateChecked = pyqtSignal(object, object)   # (release dict|None, error str|None)
        updateAuto = pyqtSignal(object, object)      # 后台自动检查结果
        dlProgress = pyqtSignal(int)                 # 后台下载进度
        dlDone = pyqtSignal(str, object)             # 后台下载完成 (path|"", err|None)
        aiSetupLog = pyqtSignal(str)                 # AI 组件安装进度文字
        aiSetupDone = pyqtSignal(object)             # AI 组件安装完成 (err str|None)

        def __init__(self):
            super().__init__()
            self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
            self.resize(1100, 700)
            try:
                self.ffmpeg = get_ffmpeg()
            except RuntimeError as e:
                QMessageBox.critical(self, APP_NAME, str(e))
                raise
            self.video_path = None
            self.duration = 0.0
            self.vw = self.vh = 0
            self._grabbing = False
            self._pending_t = 0.0
            self._cur_t = 0.0
            self.worker = None
            self.regions = []   # [{'mode','r0','t0','t1','r1'}]
            self._pending_update = None   # 已预下载完成的安装包路径
            self._auto_tag = None
            self._auto_body = ""
            self._sel = -1
            self.schemes = load_schemes()
            self._build_ui()
            self.frameGrabbed.connect(self._on_frame)
            self.updateChecked.connect(self._on_update_checked)
            self.updateAuto.connect(self._on_auto_checked)
            self.dlProgress.connect(self._on_dl_progress)
            self.dlDone.connect(self._on_auto_downloaded)
            self.aiSetupLog.connect(self._on_ai_setup_log)
            self.aiSetupDone.connect(self._on_ai_setup_done)
            # 安装版启动 4 秒后在后台静默检查更新
            if getattr(sys, "frozen", False):
                QTimer.singleShot(4000, self._auto_check)

        # ---------- UI ----------
        def _build_ui(self):
            self.preview = PreviewLabel()
            self.preview.rectDrawn.connect(self._on_rect_drawn)
            self.preview.rectTransformed.connect(self._on_rect_transformed)
            self.preview.selectionChanged.connect(self._on_selection)
            self.preview.deleteRequested.connect(self._on_rect_deleted)

            self.slider = QSlider(Qt.Horizontal)
            self.slider.setRange(0, 1000)
            self.slider.setEnabled(False)
            self.slider.valueChanged.connect(self._on_slider)
            self.lbl_time = QLabel("00:00 / 00:00")
            self.lbl_time.setMinimumWidth(110)

            left = QWidget()
            lv = QVBoxLayout(left)
            lv.setContentsMargins(6, 6, 6, 6)
            lv.addWidget(self.preview, 1)
            row = QHBoxLayout()
            row.addWidget(self.slider, 1)
            row.addWidget(self.lbl_time)
            lv.addLayout(row)

            right = QWidget()
            right.setFixedWidth(340)
            rv = QVBoxLayout(right)
            rv.setContentsMargins(8, 8, 8, 8)

            self.btn_open = QPushButton("① 选择视频文件…")
            self.btn_open.setMinimumHeight(34)
            self.btn_open.clicked.connect(self.open_video)
            rv.addWidget(self.btn_open)
            self.lbl_info = QLabel("未加载视频")
            self.lbl_info.setWordWrap(True)
            self.lbl_info.setStyleSheet("color:#555;")
            rv.addWidget(self.lbl_info)

            # --- 框选区 ---
            gb_mark = QGroupBox("② 框选水印（可框多个）")
            mv = QVBoxLayout(gb_mark)
            mv.addWidget(QLabel("预设大概位置（点击新增一个框）："))
            self.cmb_preset = QComboBox()
            self.cmb_preset.addItems(PRESETS)
            self.cmb_preset.activated.connect(self._on_preset)
            mv.addWidget(self.cmb_preset)

            self.list_rects = QListWidget()
            self.list_rects.setMaximumHeight(110)
            self.list_rects.currentRowChanged.connect(self._on_list_select)
            mv.addWidget(self.list_rects)
            brow = QHBoxLayout()
            btn_del = QPushButton("删除选中框")
            btn_del.clicked.connect(lambda: self._on_rect_deleted(self._sel))
            btn_clear = QPushButton("清空全部")
            btn_clear.clicked.connect(self._clear_all)
            brow.addWidget(btn_del)
            brow.addWidget(btn_clear)
            mv.addLayout(brow)
            tip = QLabel("操作：空白处拖动=新增框；按住框=移动；"
                         "拖红框四角=调大小；右键/Delete=删除。")
            tip.setWordWrap(True)
            tip.setStyleSheet("color:#888;font-size:11px;")
            mv.addWidget(tip)
            rv.addWidget(gb_mark)

            # --- 方案 ---
            gb_sch = QGroupBox("方案（同水印一键套用）")
            cv = QVBoxLayout(gb_sch)
            self.cmb_scheme = QComboBox()
            self.cmb_scheme.activated.connect(self._on_scheme_pick)
            cv.addWidget(self.cmb_scheme)
            srow = QHBoxLayout()
            btn_save = QPushButton("保存当前为方案")
            btn_save.clicked.connect(self._save_scheme)
            btn_del_sch = QPushButton("删除方案")
            btn_del_sch.clicked.connect(self._delete_scheme)
            srow.addWidget(btn_save, 1)
            srow.addWidget(btn_del_sch)
            cv.addLayout(srow)
            rv.addWidget(gb_sch)
            self._refresh_schemes()

            # --- 选中框设置 ---
            gb_sel = QGroupBox("选中框设置")
            sv = QVBoxLayout(gb_sel)
            self.lbl_sel = QLabel("未选中框（下面样式将作为新框默认）")
            self.lbl_sel.setStyleSheet("color:#2d8cf0;")
            sv.addWidget(self.lbl_sel)
            sv.addWidget(QLabel("水印样式："))
            self.cmb_style = QComboBox()
            for name, _ in STYLE_MODES:
                self.cmb_style.addItem(name)
            self.cmb_style.currentIndexChanged.connect(self._on_style_changed)
            sv.addWidget(self.cmb_style)

            trow = QHBoxLayout()
            self.lbl_trange = QLabel("生效时间：全程")
            trow.addWidget(self.lbl_trange, 1)
            btn_t0 = QPushButton("当前帧→开始")
            btn_t0.clicked.connect(self._set_t0)
            btn_t1 = QPushButton("当前帧→结束")
            btn_t1.clicked.connect(self._set_t1)
            btn_tclr = QPushButton("全程")
            btn_tclr.clicked.connect(self._clear_trange)
            trow.addWidget(btn_t0)
            trow.addWidget(btn_t1)
            trow.addWidget(btn_tclr)
            sv.addLayout(trow)

            mrow = QHBoxLayout()
            self.lbl_move = QLabel("移动水印：未启用")
            mrow.addWidget(self.lbl_move, 1)
            btn_r1clr = QPushButton("取消移动")
            btn_r1clr.clicked.connect(self._clear_r1)
            mrow.addWidget(btn_r1clr)
            sv.addLayout(mrow)
            tip2 = QLabel("移动水印用法：在开始处框好位置→「当前帧→开始」；"
                          "拖时间轴到结束处→「当前帧→结束」→拖动红框对准"
                          "新位置（自动记为结束位置，线性跟随移动）。")
            tip2.setWordWrap(True)
            tip2.setStyleSheet("color:#888;font-size:11px;")
            sv.addWidget(tip2)
            rv.addWidget(gb_sel)

            # --- 输出 ---
            gb_out = QGroupBox("③ 输出位置")
            ov = QHBoxLayout(gb_out)
            self.ed_out = QLineEdit()
            self.ed_out.setPlaceholderText("默认：源目录/原名_去水印.mp4")
            btn_out = QPushButton("浏览…")
            btn_out.clicked.connect(self.choose_output)
            ov.addWidget(self.ed_out, 1)
            ov.addWidget(btn_out)
            rv.addWidget(gb_out)

            self.btn_start = QPushButton("开始处理")
            self.btn_start.setMinimumHeight(38)
            self.btn_start.setStyleSheet(
                "QPushButton{background:#2d8cf0;color:white;font-weight:bold;border-radius:4px;}"
                "QPushButton:disabled{background:#9cc4ee;}")
            self.btn_start.clicked.connect(self.start_process)
            rv.addWidget(self.btn_start)

            self.btn_batch = QPushButton("批量处理（多视频 → 目录）…")
            self.btn_batch.setMinimumHeight(30)
            self.btn_batch.clicked.connect(self.open_batch)
            rv.addWidget(self.btn_batch)

            self.btn_cancel = QPushButton("取消处理")
            self.btn_cancel.setVisible(False)
            self.btn_cancel.clicked.connect(self.cancel_process)
            rv.addWidget(self.btn_cancel)

            self.progress = QProgressBar()
            self.progress.setRange(0, 100)
            rv.addWidget(self.progress)
            self.lbl_status = QLabel("就绪")
            self.lbl_status.setWordWrap(True)
            self.lbl_status.setStyleSheet("color:#555;")
            rv.addWidget(self.lbl_status)
            rv.addStretch(1)

            urow = QHBoxLayout()
            lbl_ver = QLabel(f"v{APP_VERSION}")
            lbl_ver.setStyleSheet("color:#999;")
            self.btn_update = QPushButton("检查更新")
            self.btn_update.clicked.connect(self.check_update)
            urow.addWidget(lbl_ver)
            urow.addStretch(1)
            urow.addWidget(self.btn_update)
            rv.addLayout(urow)

            sp = QSplitter(Qt.Horizontal)
            sp.addWidget(left)
            sp.addWidget(right)
            sp.setStretchFactor(0, 1)
            sp.setStretchFactor(1, 0)
            self.setCentralWidget(sp)
            self._refresh_panel()

        # ---------- 方案 ----------
        def _refresh_schemes(self, select_name=None):
            self.cmb_scheme.blockSignals(True)
            self.cmb_scheme.clear()
            self.cmb_scheme.addItem("（选择已保存的方案套用）")
            for s in self.schemes:
                self.cmb_scheme.addItem(
                    f"{s['name']}（{len(s.get('regions', []))} 个框）")
            if select_name:
                for i, s in enumerate(self.schemes):
                    if s["name"] == select_name:
                        self.cmb_scheme.setCurrentIndex(i + 1)
                        break
            self.cmb_scheme.blockSignals(False)

        def _save_scheme(self):
            if not self.regions:
                QMessageBox.warning(self, APP_NAME, "当前没有框选任何水印区域")
                return
            n = len(self.schemes) + 1
            existing = {s["name"] for s in self.schemes}
            while f"方案{n}" in existing:
                n += 1
            name, ok = QInputDialog.getText(
                self, "保存方案", "方案名称：", text=f"方案{n}")
            if not ok or not name.strip():
                return
            name = name.strip()
            if name in existing:
                QMessageBox.warning(self, APP_NAME, "已存在同名方案")
                return
            regions = []
            for r in self.regions:
                regions.append({
                    "mode": r["mode"],
                    "r0": [int(v) for v in r["r0"]],
                    "t0": r.get("t0", 0.0) or 0.0,
                    "t1": r.get("t1"),
                    "r1": [int(v) for v in r["r1"]] if r.get("r1") else None,
                })
            self.schemes.append({"name": name, "vw": self.vw, "vh": self.vh,
                                 "regions": regions})
            try:
                save_schemes(self.schemes)
            except Exception as e:
                QMessageBox.warning(self, APP_NAME, f"方案保存失败：{e}")
                return
            self._refresh_schemes(select_name=name)
            self.lbl_status.setText(
                f"方案「{name}」已保存（{len(regions)} 个框）")

        def _on_scheme_pick(self, idx):
            if idx <= 0:
                return
            scheme = self.schemes[idx - 1]
            if self.vw == 0:
                QMessageBox.warning(self, APP_NAME,
                                    "请先加载一个视频，再套用方案")
                self.cmb_scheme.blockSignals(True)
                self.cmb_scheme.setCurrentIndex(0)
                self.cmb_scheme.blockSignals(False)
                return
            self.regions = scale_regions(scheme["regions"],
                                         scheme.get("vw") or self.vw,
                                         scheme.get("vh") or self.vh,
                                         self.vw, self.vh)
            for r in self.regions:
                r["r0"] = tuple(r["r0"])
                if r.get("r1"):
                    r["r1"] = tuple(r["r1"])
            self._sel = len(self.regions) - 1 if self.regions else -1
            self._refresh_view()
            self._refresh_panel()
            self.lbl_status.setText(
                f"已套用方案「{scheme['name']}」（{len(self.regions)} 个框）")

        def _delete_scheme(self):
            idx = self.cmb_scheme.currentIndex()
            if idx <= 0:
                QMessageBox.information(self, APP_NAME, "请先在下拉框中选择要删除的方案")
                return
            scheme = self.schemes[idx - 1]
            ret = QMessageBox.question(self, APP_NAME,
                                       f"确定删除方案「{scheme['name']}」？")
            if ret != QMessageBox.Yes:
                return
            del self.schemes[idx - 1]
            try:
                save_schemes(self.schemes)
            except Exception as e:
                QMessageBox.warning(self, APP_NAME, f"保存失败：{e}")
            self._refresh_schemes()

        # ---------- 区域模型 ----------
        def _display_items(self):
            items = []
            for reg in self.regions:
                x, y, w, h = rect_at(reg, self._cur_t)
                if self.vw:
                    x, y, w, h = clamp_rect(x, y, w, h, self.vw, self.vh)
                items.append((QRect(int(x), int(y), int(w), int(h)),
                              region_active(reg, self._cur_t)))
            return items

        def _refresh_view(self):
            self.preview.set_items(self._display_items(), self._sel)
            self._refresh_list()

        def _refresh_list(self):
            self.list_rects.blockSignals(True)
            self.list_rects.clear()
            for i, reg in enumerate(self.regions):
                x, y, w, h = [int(v) for v in reg["r0"]]
                t0 = reg.get("t0", 0.0) or 0.0
                t1 = reg.get("t1")
                tr = "全程" if (t0 <= 0 and t1 is None) else \
                    f"{self._fmt_time(t0)}-{self._fmt_time(t1) if t1 is not None else '结尾'}"
                mv = "·移动" if reg.get("r1") else ""
                self.list_rects.addItem(
                    f"#{i + 1} x={x} y={y} w={w} h={h}"
                    f"　{MODE_NAMES.get(reg['mode'])}　{tr}{mv}")
            self.list_rects.blockSignals(False)
            self.list_rects.setCurrentRow(self._sel)

        def _refresh_panel(self):
            has = 0 <= self._sel < len(self.regions)
            if has:
                reg = self.regions[self._sel]
                self.lbl_sel.setText(f"正在设置：#{self._sel + 1} 号框")
                for i, (_, m) in enumerate(STYLE_MODES):
                    if m == reg["mode"]:
                        self.cmb_style.blockSignals(True)
                        self.cmb_style.setCurrentIndex(i)
                        self.cmb_style.blockSignals(False)
                        break
                t0 = reg.get("t0", 0.0) or 0.0
                t1 = reg.get("t1")
                self.lbl_trange.setText(
                    "生效时间：全程" if (t0 <= 0 and t1 is None)
                    else f"生效：{self._fmt_time(t0)} ~ "
                         f"{self._fmt_time(t1) if t1 is not None else '结尾'}")
                self.lbl_move.setText(
                    "移动水印：已启用" if reg.get("r1") else "移动水印：未启用")
            else:
                self.lbl_sel.setText("未选中框（下面样式将作为新框默认）")
                self.lbl_trange.setText("生效时间：全程")
                self.lbl_move.setText("移动水印：未启用")

        # ---------- 框事件 ----------
        def _on_rect_drawn(self, rect):
            mode = STYLE_MODES[self.cmb_style.currentIndex()][1]
            self.regions.append({"mode": mode,
                                 "r0": (rect.x(), rect.y(),
                                        rect.width(), rect.height()),
                                 "t0": 0.0, "t1": None, "r1": None})
            self._sel = len(self.regions) - 1
            self._refresh_view()
            self._refresh_panel()
            self.lbl_status.setText(f"已框选 {len(self.regions)} 个水印区域")

        def _on_rect_transformed(self, idx, rect):
            if not (0 <= idx < len(self.regions)):
                return
            reg = self.regions[idx]
            new = (rect.x(), rect.y(), rect.width(), rect.height())
            if reg.get("t1") is not None:
                # 已设定时间范围：改动写到时间较近的一端（自动形成移动）
                t0 = reg.get("t0", 0.0) or 0.0
                t1 = reg.get("t1") or self.duration or 0.0
                mid = (t0 + t1) / 2.0
                if self._cur_t <= mid:
                    reg["r0"] = new
                else:
                    reg["r1"] = new
            else:
                reg["r0"] = new
            self._refresh_list()

        def _on_rect_deleted(self, idx):
            if 0 <= idx < len(self.regions):
                del self.regions[idx]
                self._sel = -1
                self._refresh_view()
                self._refresh_panel()
                self.lbl_status.setText(
                    f"已框选 {len(self.regions)} 个水印区域" if self.regions
                    else "尚未框选水印区域")

        def _clear_all(self):
            self.regions = []
            self._sel = -1
            self._refresh_view()
            self._refresh_panel()

        def _on_selection(self, idx):
            self._sel = idx if 0 <= idx < len(self.regions) else -1
            self._refresh_view()
            self._refresh_panel()

        def _on_list_select(self, row):
            if row != self._sel and 0 <= row < len(self.regions):
                self._sel = row
                self._refresh_view()
                self._refresh_panel()

        def _on_style_changed(self, idx):
            if 0 <= self._sel < len(self.regions):
                self.regions[self._sel]["mode"] = STYLE_MODES[idx][1]
                self._refresh_list()
                self.lbl_status.setText(
                    f"#{self._sel + 1} 号框样式已改为"
                    f"「{MODE_NAMES[STYLE_MODES[idx][1]]}」")

        def _set_t0(self):
            if 0 <= self._sel < len(self.regions):
                reg = self.regions[self._sel]
                reg["t0"] = round(self._cur_t, 2)
                if reg.get("t1") is not None and reg["t1"] < reg["t0"]:
                    reg["t1"] = reg["t0"]
                self._refresh_view()
                self._refresh_panel()

        def _set_t1(self):
            if 0 <= self._sel < len(self.regions):
                reg = self.regions[self._sel]
                reg["t1"] = round(max(self._cur_t,
                                      reg.get("t0", 0.0) or 0.0), 2)
                self._refresh_view()
                self._refresh_panel()

        def _clear_trange(self):
            if 0 <= self._sel < len(self.regions):
                self.regions[self._sel]["t0"] = 0.0
                self.regions[self._sel]["t1"] = None
                self._refresh_view()
                self._refresh_panel()

        def _clear_r1(self):
            if 0 <= self._sel < len(self.regions):
                self.regions[self._sel]["r1"] = None
                self._refresh_view()
                self._refresh_panel()

        def _on_preset(self, idx):
            if idx == 0 or self.vw == 0:
                return
            w = max(60, int(self.vw * 0.25))
            h = max(40, int(self.vh * 0.12))
            mx = int(self.vw * 0.03)
            my = int(self.vh * 0.03)
            pos = {
                1: (mx, my),
                2: (self.vw - w - mx, my),
                3: (mx, self.vh - h - my),
                4: (self.vw - w - mx, self.vh - h - my),
                5: ((self.vw - w) // 2, my),
                6: ((self.vw - w) // 2, self.vh - h - my),
                7: (0, 0),
            }
            if idx == 7:
                w, h = int(self.vw * 0.30), int(self.vh * 0.15)
                pos[7] = ((self.vw - w) // 2, (self.vh - h) // 2)
            x, y = pos.get(idx, (mx, my))
            x, y, w, h = clamp_rect(x, y, w, h, self.vw, self.vh)
            mode = STYLE_MODES[self.cmb_style.currentIndex()][1]
            self.regions.append({"mode": mode, "r0": (x, y, w, h),
                                 "t0": 0.0, "t1": None, "r1": None})
            self._sel = len(self.regions) - 1
            self._refresh_view()
            self._refresh_panel()
            self.lbl_status.setText(f"已框选 {len(self.regions)} 个水印区域")
            self.cmb_preset.blockSignals(True)
            self.cmb_preset.setCurrentIndex(0)
            self.cmb_preset.blockSignals(False)

        # ---------- 视频加载 / 预览 ----------
        def open_video(self):
            path, _ = QFileDialog.getOpenFileName(self, "选择视频文件", "", VIDEO_FILTER)
            if not path:
                return
            self.video_path = path
            self.duration = get_duration(self.ffmpeg, path)
            self.slider.setEnabled(True)
            self.slider.setValue(0)
            stem, _ = os.path.splitext(os.path.basename(path))
            self.ed_out.setText(os.path.join(os.path.dirname(path), stem + "_去水印.mp4"))
            self._clear_all()
            self._grabbing = False
            self._request_frame(0.0)

        def choose_output(self):
            default = self.ed_out.text().strip() or "output_去水印.mp4"
            path, _ = QFileDialog.getSaveFileName(self, "选择输出位置", default,
                                                  "MP4 视频 (*.mp4);;所有文件 (*)")
            if path:
                self.ed_out.setText(path)

        def _on_slider(self, v):
            if self.video_path and self.duration > 0:
                self._request_frame(self.duration * v / 1000.0)

        def _request_frame(self, t):
            self._pending_t = t
            if self._grabbing:
                return
            self._grabbing = True
            cur = t
            ff, vp = self.ffmpeg, self.video_path

            def job():
                data = grab_frame_bytes(ff, vp, cur)
                self.frameGrabbed.emit(data, cur)

            threading.Thread(target=job, daemon=True).start()

        def _on_frame(self, data, t):
            self._grabbing = False
            self._cur_t = t
            if data:
                img = QImage.fromData(data)
                if not img.isNull():
                    if self.vw != img.width() or self.vh != img.height():
                        self.vw, self.vh = img.width(), img.height()
                        self._update_info()
                    self.preview.setText("")
                    self.preview.set_frame(img)
            self._update_time_label(t)
            self._refresh_view()
            if abs(self._pending_t - t) > 1e-6:
                self._request_frame(self._pending_t)

        def _update_info(self):
            name = os.path.basename(self.video_path or "")
            self.lbl_info.setText(
                f"{name}\n分辨率：{self.vw}×{self.vh}　时长：{self._fmt_time(self.duration)}")

        def _update_time_label(self, t):
            self.lbl_time.setText(
                f"{self._fmt_time(t)} / {self._fmt_time(self.duration)}")

        @staticmethod
        def _fmt_time(s):
            if s is None:
                return "结尾"
            s = max(0, int(s))
            return f"{s // 60:02d}:{s % 60:02d}"

        # ---------- 处理 ----------
        def start_process(self):
            if not self.video_path:
                QMessageBox.warning(self, APP_NAME, "请先选择视频文件")
                return
            if not self.regions:
                QMessageBox.warning(self, APP_NAME,
                                    "请先框选至少一个水印区域（可框多个）")
                return
            outp = self.ed_out.text().strip()
            if not outp:
                stem, _ = os.path.splitext(os.path.basename(self.video_path))
                outp = os.path.join(os.path.dirname(self.video_path), stem + "_去水印.mp4")
                self.ed_out.setText(outp)
            outdir = os.path.dirname(os.path.abspath(outp))
            try:
                os.makedirs(outdir, exist_ok=True)
            except Exception as e:
                QMessageBox.warning(self, APP_NAME, f"输出目录不可写：{e}")
                return
            regions = [dict(r) for r in self.regions]
            if has_ai_region(regions) and not ai_ready():
                r = QMessageBox.question(
                    self, APP_NAME,
                    "你选择了「AI 精修」样式。\n\n"
                    "首次使用需要下载 AI 组件（PyTorch + LaMa 模型，约 1GB），"
                    "只需下载一次，之后离线可用、不消耗任何 token。\n\n"
                    "是否现在下载？",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
                if r != QMessageBox.Yes:
                    return
                self._begin_ai_setup(regions, outp)
                return
            self._begin_process(regions, outp)

        # ---------- AI 组件按需下载 ----------
        def _begin_ai_setup(self, regions, outp):
            self._ai_pending = (regions, outp)
            self._ai_prog = QProgressDialog(
                "正在准备 AI 组件…", "取消", 0, 0, self)
            self._ai_prog.setWindowTitle(APP_NAME)
            self._ai_prog.setWindowModality(Qt.WindowModal)
            self._ai_prog.setMinimumDuration(0)
            self._ai_prog.setValue(0)
            self._ai_cancel = threading.Event()

            def job():
                err = None
                try:
                    ensure_ai_component(log=self.aiSetupLog.emit,
                                        cancel=self._ai_cancel)
                except Exception as e:
                    err = str(e)
                self.aiSetupDone.emit(err)

            self._ai_prog.canceled.connect(self._ai_cancel.set)
            threading.Thread(target=job, daemon=True).start()
            self._ai_prog.show()

        def _on_ai_setup_log(self, text):
            self.lbl_status.setText(text)
            if getattr(self, "_ai_prog", None):
                self._ai_prog.setLabelText(text)

        def _on_ai_setup_done(self, err):
            if getattr(self, "_ai_prog", None):
                self._ai_prog.close()
                self._ai_prog = None
            pending = getattr(self, "_ai_pending", None)
            self._ai_pending = None
            if err:
                self.lbl_status.setText("AI 组件安装未完成")
                if err != "已取消":
                    QMessageBox.warning(
                        self, APP_NAME,
                        "AI 组件下载/安装失败：\n" + err +
                        "\n\n请检查网络后重试（已下载的部分不会重复下载）。")
                return
            QMessageBox.information(self, APP_NAME,
                                    "AI 组件安装完成，开始处理视频。")
            if pending:
                self._begin_process(*pending)

        def _begin_process(self, regions, outp):
            self.worker = ProcessThread(self.ffmpeg, self.video_path, outp, regions)
            self.worker.progress.connect(self.progress.setValue)
            self.worker.status.connect(self.lbl_status.setText)
            self.worker.finished_.connect(self._on_done)
            self._set_running(True)
            n_inpaint = sum(1 for r in regions if r["mode"] == "inpaint")
            tip = f"正在处理 {len(regions)} 个水印区域"
            if n_inpaint:
                tip += "（含智能修复，速度较慢属正常）"
            if has_ai_region(regions):
                tip += "（AI 精修逐帧运行，速度较慢属正常）"
            self.lbl_status.setText(tip + "…")
            self.worker.start()

        def open_batch(self):
            if not self.regions:
                QMessageBox.warning(self, APP_NAME,
                                    "请先框选水印区域（或套用已保存的方案）再批量处理")
                return
            if not self.vw:
                QMessageBox.warning(self, APP_NAME, "请先加载一个视频作为参考")
                return
            dlg = BatchDialog(self, self.ffmpeg, self.regions,
                              (self.vw, self.vh))
            dlg.exec_()

        def cancel_process(self):
            if self.worker:
                self.worker.cancel_ev.set()
                self.lbl_status.setText("正在取消…")

        def _set_running(self, running):
            for w in (self.btn_open, self.btn_start, self.btn_batch,
                      self.cmb_preset, self.cmb_style, self.ed_out,
                      self.slider, self.list_rects, self.cmb_scheme):
                w.setEnabled(not running)
            self.btn_cancel.setVisible(running)

        # ---------- 在线升级 ----------
        # ---------- 后台自动更新 ----------
        def _update_state_path(self):
            return os.path.join(config_dir(), "update_state.json")

        def _load_update_state(self):
            try:
                with open(self._update_state_path(), encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}

        def _save_update_state(self, st):
            try:
                with open(self._update_state_path(), "w", encoding="utf-8") as f:
                    json.dump(st, f, ensure_ascii=False)
            except Exception:
                pass

        def _auto_check(self):
            """启动后后台静默检查更新；失败不打扰用户。"""
            def job():
                try:
                    self.updateAuto.emit(fetch_latest_release(), None)
                except Exception as e:
                    self.updateAuto.emit(None, str(e))

            threading.Thread(target=job, daemon=True).start()

        def _on_auto_checked(self, release, err):
            if err or not release:
                return  # 静默忽略
            tag = release.get("tag_name", "")
            if parse_version(tag) <= parse_version(APP_VERSION):
                return
            if self._load_update_state().get("skipped") == tag:
                return  # 用户已跳过该版本
            asset = pick_asset(release)
            if not asset:
                return
            self._auto_tag = tag
            self._auto_body = (release.get("body") or "").strip()
            dst = os.path.join(tempfile.gettempdir(), asset["name"])
            # 已完整下载过则直接提示
            if os.path.isfile(dst) and os.path.getsize(dst) == asset.get("size"):
                self.dlDone.emit(dst, None)
                return
            self.lbl_status.setText(f"发现新版本 {tag}，正在后台下载…")

            def job():
                try:
                    download_file(asset["browser_download_url"], dst,
                                  progress_cb=self.dlProgress.emit)
                    self.dlDone.emit(dst, None)
                except Exception as e:
                    self.dlDone.emit("", str(e))

            threading.Thread(target=job, daemon=True).start()

        def _on_dl_progress(self, p):
            if self._auto_tag:
                self.lbl_status.setText(
                    f"正在后台下载新版本 {self._auto_tag}… {p}%")

        def _on_auto_downloaded(self, path, err):
            if err or not path:
                self.lbl_status.setText("就绪")
                return
            self._pending_update = path
            self.lbl_status.setText(f"新版本 {self._auto_tag} 已就绪，可升级")
            box = QMessageBox(self)
            box.setWindowTitle(APP_NAME)
            box.setIcon(QMessageBox.Information)
            box.setText(f"新版本 {self._auto_tag} 已下载完成！\n\n"
                        "点击「立即升级」将关闭程序并自动完成安装，"
                        "随后自动启动新版本。")
            if self._auto_body:
                box.setDetailedText(self._auto_body)
            btn_now = box.addButton("立即升级", QMessageBox.AcceptRole)
            box.addButton("下次再说", QMessageBox.RejectRole)
            btn_skip = box.addButton("跳过此版本", QMessageBox.DestructiveRole)
            box.exec_()
            c = box.clickedButton()
            if c is btn_now:
                self._apply_upgrade(path)
            elif c is btn_skip:
                st = self._load_update_state()
                st["skipped"] = self._auto_tag
                self._save_update_state(st)
                self._pending_update = None
                self.lbl_status.setText(f"已跳过 {self._auto_tag}")

        def check_update(self):
            self.btn_update.setEnabled(False)
            self.lbl_status.setText("正在检查更新…")

            def job():
                try:
                    self.updateChecked.emit(fetch_latest_release(), None)
                except Exception as e:
                    self.updateChecked.emit(None, str(e))

            threading.Thread(target=job, daemon=True).start()

        def _on_update_checked(self, release, err):
            self.btn_update.setEnabled(True)
            if err:
                self.lbl_status.setText("检查更新失败")
                QMessageBox.warning(
                    self, APP_NAME,
                    "检查更新失败，请确认能正常访问 github.com 后重试。\n\n" + err)
                return
            latest = parse_version(release.get("tag_name", ""))
            current = parse_version(APP_VERSION)
            if latest <= current:
                self.lbl_status.setText(f"已是最新版本 v{APP_VERSION}")
                QMessageBox.information(self, APP_NAME,
                                        f"当前已是最新版本（v{APP_VERSION}）。")
                return
            asset = pick_asset(release)
            if not asset:
                QMessageBox.warning(self, APP_NAME,
                                    "最新版本没有找到安装包资产，请稍后再试。")
                return
            body = (release.get("body") or "").strip() or "（无更新说明）"
            size_mb = (asset.get("size") or 0) / 1048576
            box = QMessageBox(self)
            box.setWindowTitle(APP_NAME)
            box.setIcon(QMessageBox.Question)
            box.setText(f"发现新版本 {release.get('tag_name')}！\n"
                        f"当前版本 v{APP_VERSION}\n"
                        f"安装包：{asset['name']}（约 {size_mb:.0f} MB）")
            box.setDetailedText(body)
            btn_yes = box.addButton("立即升级", QMessageBox.AcceptRole)
            box.addButton("以后再说", QMessageBox.RejectRole)
            box.exec_()
            if box.clickedButton() is btn_yes:
                self._download_upgrade(asset)

        def _download_upgrade(self, asset):
            if not getattr(sys, "frozen", False):
                QMessageBox.information(self, APP_NAME,
                                        "开发模式不执行升级（请用安装版测试）。")
                return
            from PyQt5.QtWidgets import QProgressDialog
            dlg = QProgressDialog("正在下载新版本…", "取消", 0, 100, self)
            dlg.setWindowTitle(APP_NAME)
            dlg.setWindowModality(Qt.WindowModal)
            dlg.setMinimumDuration(0)
            dlg.setValue(0)
            cancel_ev = threading.Event()
            dlg.canceled.connect(cancel_ev.set)
            dst = os.path.join(tempfile.gettempdir(), asset["name"])
            # 后台已预下载完成（大小一致）则直接安装，不重复下载
            if (os.path.isfile(dst)
                    and os.path.getsize(dst) == (asset.get("size") or -1)):
                dlg.close()
                self._apply_upgrade(dst)
                return
            result = {}

            def job():
                try:
                    download_file(asset["browser_download_url"], dst,
                                  progress_cb=lambda p: dlg.setValue(p),
                                  cancel=cancel_ev)
                    result["ok"] = True
                except Exception as e:
                    result["err"] = str(e)

            th = threading.Thread(target=job, daemon=True)
            th.start()
            while th.is_alive():
                QApplication.processEvents()
                th.join(0.05)
            dlg.close()
            if not result.get("ok"):
                if result.get("err") != "已取消":
                    QMessageBox.warning(self, APP_NAME,
                                        "下载失败：" + result.get("err", ""))
                self.lbl_status.setText("升级已取消" if result.get("err") == "已取消"
                                        else "下载失败")
                return
            self._apply_upgrade(dst)

        def _apply_upgrade(self, setup_path):
            instdir = os.path.dirname(os.path.abspath(sys.executable))
            app_exe = os.path.join(instdir, "视频水印擦除工具.exe")
            bat = build_update_bat(setup_path, instdir, app_exe)
            QMessageBox.information(
                self, APP_NAME,
                "下载完成，点击确定后程序将关闭并自动升级，随后自动启动新版本。")
            subprocess.Popen(["cmd", "/c", bat],
                             creationflags=_CREATE_NO_WINDOW | 0x00000008,
                             close_fds=True)
            QApplication.quit()

        def _on_done(self, ok, msg):
            self._set_running(False)
            if ok:
                self.progress.setValue(100)
                self.lbl_status.setText(f"处理完成：{msg}")
                box = QMessageBox(self)
                box.setWindowTitle(APP_NAME)
                box.setIcon(QMessageBox.Information)
                box.setText(f"处理完成！\n输出文件：{msg}")
                btn_open = box.addButton("打开所在文件夹", QMessageBox.AcceptRole)
                box.addButton("关闭", QMessageBox.RejectRole)
                box.exec_()
                if box.clickedButton() is btn_open:
                    os.startfile(os.path.dirname(os.path.abspath(msg)))
            else:
                self.lbl_status.setText(msg)
                if msg != "已取消":
                    QMessageBox.warning(self, APP_NAME, msg)
                else:
                    self.progress.setValue(0)
            self.worker = None

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--cli", action="store_true", help="命令行模式（无界面）")
    parser.add_argument("--input", help="输入视频路径")
    parser.add_argument("--output", help="输出视频路径")
    parser.add_argument("--rect", action="append",
                        help="x,y,w,h[,mode[,t0,t1[,x1,y1]]]，可重复多次")
    parser.add_argument("--mode", choices=["inpaint", "blur", "mosaic", "delogo",
                                           "inpaint_ai"],
                        default="inpaint", help="默认擦除方式（rect 未指定时）")
    args = parser.parse_args()
    if args.cli:
        if not (args.input and args.output and args.rect):
            parser.error("--cli 模式需要 --input --output 以及至少一个 --rect")
        sys.exit(cli_main(args))
    gui_main()


if __name__ == "__main__":
    main()
