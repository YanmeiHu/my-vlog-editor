"""Mobile-first Chinese video editor."""
import hashlib
from pathlib import Path
import tempfile

import streamlit as st

from vlog_editor.analysis import analyze, select_clips
from vlog_editor.media import Clip, MediaError, check_tools, export_video, probe

st.set_page_config(page_title="轻剪 · Vlog 视频剪辑", page_icon="🎬", layout="centered")
st.markdown("""<style>
.block-container {max-width:760px;padding-top:2rem;padding-bottom:4rem;}
.stButton button,.stDownloadButton button {min-height:48px;border-radius:12px;}
[data-testid="stFileUploader"] {border-radius:16px;}
</style>""", unsafe_allow_html=True)
st.title("轻剪 AI 2.0 🎬")
st.write("把日常片段，剪成你的故事。")
st.caption("自动选段基于镜头变化与静音规则，不具备 AI 语义理解；手动剪辑仍可使用。")
try:
    check_tools()
except MediaError as exc:
    st.error(str(exc))
    st.stop()

MAX_TOTAL_UPLOAD_MB = 100
max_file_mb = st.get_option("server.maxUploadSize")
st.session_state.setdefault("upload_generation", 0)
uploads = st.file_uploader("① 添加视频", type=["mp4", "mov"], accept_multiple_files=True, key=f"uploads-{st.session_state['upload_generation']}",
                           help=f"支持 MP4 / MOV；最多 12 个文件，单个 {max_file_mb} MB，总计 {MAX_TOTAL_UPLOAD_MB} MB。")
st.caption("素材会上传到运行本应用的服务器。请只上传你有权处理的视频。")
if st.button("清空素材与结果", use_container_width=True):
    generation = st.session_state["upload_generation"] + 1
    st.session_state.clear()
    st.session_state["upload_generation"] = generation
    st.rerun()
if not uploads:
    st.session_state.pop("result", None)
    st.info("先添加几段视频，再调整顺序与保留时间。")
    st.stop()
if (len(uploads) > 12
        or any(u.size > max_file_mb * 1024**2 for u in uploads)
        or sum(u.size for u in uploads) > MAX_TOTAL_UPLOAD_MB * 1024**2):
    st.session_state.pop("result", None)
    st.error(f"素材超过限制：最多 12 个，单个不超过 {max_file_mb} MB，总计不超过 {MAX_TOTAL_UPLOAD_MB} MB。")
    st.stop()

signature = hashlib.sha256(b"".join(hashlib.sha256(u.getbuffer()).digest() for u in uploads)).hexdigest()
if st.session_state.get("source_signature") != signature:
    st.session_state["source_signature"] = signature
    st.session_state["order"] = list(range(len(uploads)))
    st.session_state.pop("result", None)
    st.session_state.pop("candidates", None)


def move(index: int, delta: int) -> None:
    order = st.session_state["order"]
    order[index], order[index + delta] = order[index + delta], order[index]


try:
    # Each rerun owns its input files; the context cleans up on normal unwinding.
    # Process termination can bypass cleanup; never treat this disk as persistent storage.
    with tempfile.TemporaryDirectory(prefix="vlog-input-") as directory:
        paths = []
        infos = []
        for i, upload in enumerate(uploads):
            path = Path(directory) / f"{i}{Path(upload.name).suffix.lower()}"
            path.write_bytes(upload.getbuffer())
            paths.append(path)
            infos.append(probe(path))
        mode = st.selectbox("剪辑方式", ["手动剪辑", "自动选段"])
        clips = []
        if mode == "自动选段":
            st.caption("低分辨率采样检测镜头切换、画面变化和明显静音。候选分数不是内容精彩程度判断。")
            segment = st.slider("每段最多保留（秒）", 2, 10, 5)
            target = st.select_slider("目标成片时长（秒）", options=[15, 30, 60], value=30)
            analysis_key = (signature, segment)
            if st.button("分析素材并生成候选", use_container_width=True):
                st.session_state.pop("candidates", None)
                st.session_state.pop("result", None)
                with st.spinner("正在检测镜头、画面变化与静音…"):
                    st.session_state["candidates"] = analyze(paths, segment)
                    st.session_state["analysis_key"] = analysis_key
            if st.session_state.get("analysis_key") == analysis_key:
                candidates = st.session_state.get("candidates", [])
                clips = select_clips(candidates, paths, target)
                st.write(f"检测到 {len(candidates)} 个候选；按分数筛选后恢复素材时间顺序。")
                for clip in clips:
                    source = paths.index(clip.path)
                    st.text(f"{uploads[source].name} · {clip.start:.2f}–{clip.end:.2f} 秒")
                with st.expander("查看全部候选和筛选依据"):
                    st.dataframe([{"素材": uploads[c.source].name, "开始": c.start,
                                   "结束": c.end, "分数": round(c.score, 3), "依据": c.reason}
                                  for c in candidates], hide_index=True)
                actual = sum(c.end-c.start for c in clips)
                if actual < target - .04:
                    st.warning(f"可用素材不足 {target} 秒，将导出约 {actual:.1f} 秒，不重复或补长素材。")
            else:
                st.info("请先分析素材；更改每段保留时长后需重新分析。")
        else:
            st.subheader("② 整理你的故事")
            clips = []
            for position, i in enumerate(st.session_state["order"]):
                with st.container(border=True):
                    st.write(f"**片段 {position + 1}**")
                    st.text(uploads[i].name)
                    up, down = st.columns(2)
                    up.button("↑ 上移", key=f"up-{i}", disabled=position == 0,
                              on_click=move, args=(position, -1), use_container_width=True)
                    down.button("↓ 下移", key=f"down-{i}", disabled=position == len(uploads)-1,
                                on_click=move, args=(position, 1), use_container_width=True)
                    st.caption(f"原始时长 {infos[i].duration:.2f} 秒 · 时间单位：秒")
                    start = st.number_input("开始时间", min_value=0.0, max_value=infos[i].duration,
                                            value=0.0, step=0.1, key=f"start-{signature}-{i}")
                    end = st.number_input("结束时间", min_value=0.0, max_value=infos[i].duration,
                                          value=infos[i].duration, step=0.1, key=f"end-{signature}-{i}")
                    clips.append(Clip(paths[i], start, end))
        st.subheader("③ 导出成片")
        ratio = st.radio("画面比例", ["9:16", "16:9"], horizontal=True,
                         help="竖屏适合手机，横屏适合大屏；保留完整画面，空白处补黑边。")
        st.caption("输出 720p · H.264 / AAC · 最长 10 分钟。处理期间请保持页面打开。")
        edit_signature = (mode, signature, tuple((str(c.path.name), c.start, c.end) for c in clips), ratio)
        if st.session_state.get("edit_signature") != edit_signature:
            st.session_state.pop("result", None)
        st.session_state["edit_signature"] = edit_signature
        if st.button("生成我的视频", type="primary", use_container_width=True, disabled=not clips and mode != "自动选段"):
            st.session_state.pop("result", None)
            with st.spinner("正在剪辑与合并，请稍候…"):
                if mode == "自动选段" and not clips:
                    candidates = analyze(paths, segment)
                    st.session_state["candidates"] = candidates
                    st.session_state["analysis_key"] = analysis_key
                    clips = select_clips(candidates, paths, target)
                    actual = sum(c.end-c.start for c in clips)
                    if actual < target - .04:
                        st.warning(f"素材不足，将导出约 {actual:.1f} 秒。")
                    st.session_state["edit_signature"] = (mode, signature, tuple((str(c.path.name), c.start, c.end) for c in clips), ratio)
                st.session_state["result"] = export_video(clips, ratio)
        if st.session_state.get("result"):
            st.success("成片已生成，可以预览和保存了。")
            st.video(st.session_state["result"], format="video/mp4")
            st.download_button("下载 MP4 视频", data=st.session_state["result"],
                               file_name="my-vlog.mp4", mime="video/mp4", use_container_width=True)
except MediaError as exc:
    st.error(str(exc))
except OSError:
    st.error("临时存储空间不足或文件读取失败，请减少素材后重试。")
