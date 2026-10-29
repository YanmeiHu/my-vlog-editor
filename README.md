# 轻剪 · My Vlog Editor

基于 Python、Streamlit 和 FFmpeg 的中文视频剪辑 MVP，界面优先面向 iPhone / iPad 浏览器。
**目前只提供手动剪辑，不包含 AI 自动剪辑、字幕、配乐或内容分析。**

## 功能

- 上传多个 MP4 / MOV（最多 12 个；单个 200 MB；合计 500 MB）。
- 使用上移 / 下移按钮排序；以秒设置每段开始、结束时间。
- 导出 9:16（720×1280）或 16:9（1280×720），保留完整画面并补黑边。
- 统一为 30 fps、H.264 / AAC、MP4 faststart；无声素材自动补静音。
- 预览、下载 MP4；修改剪辑设置后旧结果自动失效。
- 总成片最长 10 分钟；处理失败显示中文提示。

## 本地运行

需要 Python 3.11+、带 libx264 / AAC 编码器的 FFmpeg 和 ffprobe。

```bash
# Ubuntu / Debian（如未安装 FFmpeg）
sudo apt-get update && sudo apt-get install -y ffmpeg
# macOS 可使用 brew install ffmpeg
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m streamlit run app.py --server.address 0.0.0.0 --server.port 8501
```

在电脑浏览器打开服务器地址。同一可信局域网中的 iPhone / iPad 可访问电脑的局域网 IP 和 8501 端口；生产部署应使用 HTTPS、访问控制和适当的请求/资源限制。不要直接把无认证服务开放给公网。

手机使用步骤：添加素材 → 上移/下移排序 → 输入保留时间 → 选择比例 → 生成 → 预览并下载。iOS 的下载/保存入口因 Safari 和系统版本而异；必要时从浏览器下载列表保存到“文件”。

## 测试

```bash
python -m pytest -q
```

测试通过 FFmpeg 生成有声 MOV 与无声 MP4，验证真实裁剪合并、顺序像素、两种比例、时长、音轨、无效素材/时间处理及成功和失败后的临时目录清理。另使用 Streamlit AppTest 验证首页和清空动作。

## 项目结构

```text
app.py                    中文 Streamlit 界面与会话状态
vlog_editor/media.py      ffprobe 校验和 FFmpeg 编码管线
tests/test_media.py        可执行基础测试
.streamlit/config.toml    主题、上传大小限制
requirements.txt          运行依赖
requirements-dev.txt      测试依赖
```

## 数据与限制

上传内容会传到服务器；每次界面执行的输入临时文件以及每次导出的中间文件都使用独立临时目录，在正常完成和异常退出时清理。上传缓存和成片保留在 Streamlit 会话内存中；“清空素材与结果”释放当前会话引用。服务被强制终止可能遗留操作系统临时文件，部署者应定期清理系统临时目录。不要上传敏感素材到不受信任的部署。

MVP 采用同步 CPU 编码，单段编码超时 15 分钟，合并超时 2 分钟；大文件或并发会消耗较多内存/CPU，不适合作为未经资源隔离的多租户服务。iPhone HEVC MOV 需要系统 FFmpeg 支持解码；按 FFmpeg 自动旋转处理方向，HDR 不保证色彩准确。字幕、转场、背景音乐、AI、后台任务队列、HDR 色调映射尚未实现。移动端真机体验仍需在目标 iOS / iPadOS 设备验收。

## GitHub PR

若命令行无法访问 GitHub API，推送分支后访问：

https://github.com/YanmeiHu/my-vlog-editor/compare/main...feat/mobile-video-editor-mvp?expand=1

创建 PR，检查变更和测试结果后合并到 `main`。
