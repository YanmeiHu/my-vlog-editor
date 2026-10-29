# 轻剪 AI 2.0

Python + Streamlit + FFmpeg 的中文手机端视频剪辑工具，保留原有手动剪辑。

**能力边界：自动选段是可解释的镜头/画面变化/静音规则，不是 AI 内容理解，也不能保证识别“精彩”内容。可选 Whisper 语音识别才使用机器学习模型；没有提供语义视频理解、人物/事件识别或自动文案。**

## 第一阶段：自动选段

1. 上传多个 MP4 / MOV，切换“自动选段”。
2. 设置每段最多保留 2–10 秒、目标成片 15 / 30 / 60 秒。
3. 点击“分析素材并生成候选”查看候选时间、评分和依据；或直接点击“生成我的视频”，一次完成分析、筛选和导出。
4. 选择 9:16（720×1280）或 16:9（1280×720），预览并下载 MP4。

分析以 3 fps、160 像素宽采样，检测相邻画面变化；scene score ≥ 0.35 视为切镜边界，音量低于 -35 dB 且持续至少 0.5 秒视为明显静音。有声素材的静音占比降低评分，无音轨素材仍可选。候选先按变化/声音规则排序，选中后恢复素材与时间顺序；不重复素材，不补帧凑时长。素材不足时明确提示并导出实际可用时长。快速切镜可能漏检，运动和噪声也可能被误评，应人工预览。

“手动剪辑”保持可用：上移/下移素材，输入每段开始、结束秒数，导出原有 MP4。修改剪辑设置会使旧成片失效。

## 第二阶段：字幕与配乐

先生成基础成片，再展开“字幕与背景音乐”并勾选启用：

- 可选语音识别：选择中文或 English，生成相对于**当前成片**的 SRT。使用开源 faster-whisper、多语言 Whisper tiny、CPU int8、2 线程，无需 GPU、付费 API 或密钥。
- SRT 文本框支持预览和修改时间、文字；也可直接粘贴手写 SRT。校验时间范围、顺序、重叠和内容长度，可下载 SRT。
- 上传 MP3 / WAV / M4A 背景音乐（最多 20 MB），调整原声和配乐音量。音乐过短自动循环，过长裁至成片长度；混音限幅避免削波。
- “烧录字幕并合成配乐”生成带字幕的 MP4，单独预览并下载。修改字幕/音量/音乐会使旧合成结果失效。始终保留基础成片的下载入口。

SRT 示例：

```srt
1
00:00:00,000 --> 00:00:01,500
你好，Hello!
```

Whisper tiny 可能误识别或在静音中产生幻觉，不保证字幕准确，需人工校对。不提供翻译或逐词卡拉 OK 字幕。基础成片的静音填充不等同于识别到了语音。

## 安装与启动

需要 Python 3.12（已测试）、FFmpeg / ffprobe，FFmpeg 包含 libx264、AAC、libass；中文烧录需要 Noto CJK 字体。

```bash
# Ubuntu / Debian；系统已提供时无需重复安装
sudo apt-get update
sudo apt-get install -y ffmpeg fonts-noto-cjk
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m streamlit run app.py
```

### 启用可选语音识别

基础部署不安装语音识别依赖，避免增加免费实例的内存和下载负担；此时页面禁用识别按钮，手写字幕、烧录、音乐和自动选段仍可用。

```bash
python -m pip install -r requirements-asr.txt
```

首次使用需联网从 Hugging Face 下载 `Systran/faster-whisper-tiny`，并可能访问模型文件 CDN。模型默认缓存到系统临时目录的 `vlog-whisper-models`；可用非秘密环境变量 `VLOG_MODEL_CACHE` 指定可写缓存目录。可将 `VLOG_WHISPER_MODEL` 设置为已下载、可供 CTranslate2 使用的本地模型目录，以避免在线下载。模型缓存有意复用，不保存用户音频。

每次识别在独立子进程中执行，退出后释放模型内存；最多 120 秒成片、5 分钟硬超时。临时音频随任务清理。模型下载/CPU 推理仍可能超出免费平台资源；**不保证 Community Cloud 的任意免费实例都能承载 Whisper**。没有可用模型时会显示中文错误，不会伪造识别结果。素材在应用服务器处理，不发送到付费识别服务。

## Streamlit Community Cloud

1. 仓库选择 `YanmeiHu/my-vlog-editor`，分支选择包含 2.0 提交的分支（合并后可选 `main`），入口 **`app.py`**，不要选择 `hello.py`。
2. Advanced settings 选择 **Python 3.12**。基础功能不需要 Secrets。
3. 根目录 `requirements.txt` 安装 Streamlit；`packages.txt` 安装系统 FFmpeg 和中文字体，无需自定义启动脚本。
4. 若要在线识别，在部署分支的根目录 `requirements.txt` 增加 `faster-whisper==1.2.1` 后重新部署，并保证模型下载网络可用。**不要把 `requirements-asr.txt` 再反向包含进 requirements.txt**，它已包含基础依赖，会造成递归。
5. 用短视频验证自动选段、两种比例、手动剪辑、字幕和下载；启用 ASR 后还需用真实中文和英文语音验收。模型下载与资源问题请查应用日志。

上传默认单个 **50 MB**、合计 **100 MB**、最多 **12 个素材**。总大小校验发生在上传之后，不能阻止上传阶段的全部内存占用。自动分析总素材时长最多 10 分钟，手动成片最多 10 分钟；自动成片最多 60 秒。字幕最多 500 条，每条 2000 字符，文本总计 100000 字符。

每个应用进程同时只允许一个分析/编码/识别/合成重任务，忙时提示稍后重试，不是分布式任务队列。视频帧由 FFmpeg 顺序处理，不在 Python 中加载完整帧数组。输出 H.264 / AAC、30 fps、faststart，按比例补黑边；iPhone HEVC 需要系统解码器，HDR 色调映射尚未实现。

上传、成片及 Streamlit 媒体缓存均占内存，应及时下载并清空。每次输入/编码/识别/合成使用独立临时目录，正常完成和异常回退会清理；进程被强制终止可能遗留临时文件。休眠/重启/重新部署不保留用户素材。不要公开部署敏感素材；长期任务、多用户高并发需要对象存储、鉴权和任务队列。

## 测试与验证边界

```bash
python -m pytest -q
python -m pip check
```

- 第一阶段完成时 21 项测试通过；新增真实 15/30/60 秒输出验证，避免只验证选段列表。
- 真实 FFmpeg 检测切镜/静音、两种输出尺寸、时长、顺序像素、错误处理及临时文件清理。
- 真实字幕烧录像素对比，以及 440/880 Hz 频谱验证原声和配乐确实混合。
- Streamlit AppTest 验证保留的手动流程、一键自动生成、字幕修改/合成与结果失效。
- ASR 子进程接口、超时与清理使用替身测试；可选依赖已实际安装、导入。**开发环境模型仓库请求返回代理 403，真实中文/英文模型推理未完成验证，不能视为已通过 ASR 端到端测试。**
- 尚未在 Community Cloud 新版本及 iPhone / iPad 真机上验收。

## 项目结构

```text
app.py                       中文移动端界面
vlog_editor/media.py         原有裁剪/合并管线
vlog_editor/analysis.py      规则分析与候选选段
vlog_editor/captions.py      SRT 校验、ASS 安全生成
vlog_editor/finishing.py     字幕/配乐合成、ASR 子进程调度
vlog_editor/asr_worker.py    可选 CPU Whisper 推理
vlog_editor/resources.py     单进程重任务并发限制
tests/                      媒体与页面测试
requirements*.txt            基础、开发和可选语音识别依赖
packages.txt                 系统 FFmpeg / 中文字体
.streamlit/config.toml       主题与上传限制
```
