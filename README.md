# 视频水印擦除工具

**📥 下载安装包：[视频水印擦除工具 v1.5.1 一键安装包（164MB，免联网免管理员）](https://github.com/retision-coder/video-tools/releases/latest)**

针对本地视频的水印擦除小工具（Windows）。
支持**多个水印、每框独立样式、框大小调整、关键帧移动水印跟踪（帧级精度）、播放/暂停/停止/逐帧预览、方案复用、批量处理、后台自动更新**。

## 一键安装（推荐）

双击 `installer/视频水印擦除工具_Setup_v1.5.1.exe`，向导一键安装
（无需联网、无需管理员权限，默认装到 `%LOCALAPPDATA%\Programs\`）：

- 自动创建桌面 + 开始菜单快捷方式
- 在"设置 → 应用与功能"中可见，可正常卸载（含静默卸载 `卸载.exe /S`）
- 所有依赖（含 ffmpeg、OpenCV）已内嵌在安装包里
- 你保存的水印方案存放在 `%APPDATA%\视频水印擦除工具\`，**升级覆盖安装不会丢失**

静默安装（供以后在线升级 / 批量部署调用）：

```
视频水印擦除工具_Setup_v1.5.1.exe /S /D=C:\安装目录
```

> 在线升级：主界面右下角「检查更新」→ 自动比对 GitHub Releases 最新版本 →
> 显示更新日志 → 一键下载安装包并静默覆盖安装 → 自动启动新版。
> 方案配置在 `%APPDATA%`，升级不丢失。需要能访问 github.com。

### 发布新版本（维护者）

1. 改 `main.py` / `setup.py` 的 `APP_VERSION` 和 `version_info.txt`，重跑 `build.py` + `build_installer.py`
2. 到 GitHub 仓库 [retision-coder/video-tools](https://github.com/retision-coder/video-tools)
   → Releases → Draft a new release → tag 填 `vX.Y.Z`
3. 把 `installer\视频水印擦除工具_Setup_vX.Y.Z.exe` 作为资产上传，正文写更新日志，Publish
4. 老版本用户在应用内点「检查更新」即可自动升级

## 功能

- 支持常见视频格式：mp4 / avi / mkv / mov / flv / wmv / ts / m4v / webm 等
- 预览画面 + 时间轴拖动定位，**播放 / 暂停 / 停止 / 上一帧 / 下一帧** 完整播放控制，
  时间显示精确到帧（mm:ss.cc + 帧号）
- **多水印框选**（数量不限）：
  - 空白处拖动 = 新增框；按住框 = 移动；**拖红框四角 = 调大小**；右键 / Delete = 删除
  - 7 个预设大概位置（左上 / 右上 / 左下 / 右下 / 顶部居中 / 底部居中 / 正中央），点一次新增一个框
  - 每个框单独选擦除样式（先点选框，再切换样式）
- **四种擦除样式**：
  - 文字 / Logo / 台标 → **智能修复**（OpenCV inpaint，按周边纹理填充，融合自然）
  - 追求最干净 → **AI 精修（LaMa 大模型）**，与周围环境融合最好；
    首次使用会提示下载 AI 组件（约 1GB，仅一次），之后**离线运行、不消耗任何 token**；
    CPU 逐帧修复速度较慢属正常，区域越小越快
  - 半透明水印 → 高斯模糊
  - 复杂背景水印 → 马赛克
- **关键帧跟踪（移动水印）**：在任意一帧拖动红框即自动打关键帧（可打任意多个），
  框在相邻关键帧之间线性移动，像跟踪水印一样；关键帧时间精确到帧；
  选中框的首末及中间关键帧位置以青色虚线框同时显示，一眼看清运动轨迹；
  「此处开始/结束生效」控制作用区间，「设为静止全程」一键还原固定框
- 处理有进度条，可中途取消；原音轨保留；输出位置可指定
- **方案复用**：框选配置可保存为"方案1 / 方案2 …"（存在
  `%APPDATA%\视频水印擦除工具\watermark_schemes.json`）；同一水印的视频直接下拉套用；
  方案记录保存时的分辨率，套用到其他分辨率视频时自动等比缩放
- **批量处理**：主界面「批量处理」按钮 → 添加多个视频或整个文件夹 →
  选输出目录 → 依次套用当前框选处理，导出到该目录（`原名_去水印.mp4`），
  有总进度条和每个文件的成功/失败日志

## 使用方法（exe）

双击 `dist/视频水印擦除工具.exe`，按 ①②③ 步骤操作。

移动水印（关键帧跟踪）操作：在起始帧框好 → 播放或逐帧走到水印移动后的位置 →
直接拖动红框对准（自动打关键帧）→ 重复直到覆盖全程；
「此处开始/结束生效」裁剪作用区间，「删除此时关键帧」修正误打的点。

命令行模式（`--rect` 可重复，字段 `x,y,w,h[,mode[,t0,t1[,x1,y1]]]`）：

```
视频水印擦除工具.exe --cli --input a.mp4 --output out.mp4 ^
    --rect 455,15,180,50,inpaint ^
    --rect 15,295,210,55,blur,0,6,405,295
# mode: inpaint（智能修复）/ blur（高斯模糊）/ mosaic（马赛克）/ inpaint_ai（AI 精修）
# t0,t1 为生效起止秒；x1,y1 为结束位置（线性移动，等价于 2 个关键帧）
# 注：inpaint_ai 需先在图形界面中完成 AI 组件的首次下载
```

## 从源码运行 / 自行打包

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python main.py              # 运行 GUI
.venv\Scripts\python build.py             # 打包主程序 exe（输出到 dist\）
.venv\Scripts\python build_installer.py   # 打包一键安装包（输出到 installer\）
```

依赖全部 pip 一键安装（PyQt5 / opencv-python-headless / imageio-ffmpeg / pyinstaller），
ffmpeg 由 `imageio-ffmpeg` 自动附带，无需单独安装。

## 说明

- 「智能修复」逐帧做 inpaint，速度比模糊/马赛克慢，属正常现象；区域越小越快
- 「AI 精修」使用 LaMa 大模型逐帧重建水印区域，效果最干净；AI 组件按需下载
  （PyTorch CPU + big-lama 模型，存于 `%APPDATA%\视频水印擦除工具i`），
  默认源下载失败会自动切换国内镜像
- 升级时安装向导会自动识别当前安装目录并覆盖升级，无需重新选择
