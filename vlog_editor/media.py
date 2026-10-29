"""Bounded FFmpeg jobs. Never interpolate uploaded names into shell commands."""
from dataclasses import dataclass
import json
import math
from pathlib import Path
import shutil
import subprocess
import tempfile


class MediaError(ValueError):
    """An actionable, safe-to-display media error."""


@dataclass(frozen=True)
class MediaInfo:
    duration: float
    has_audio: bool


@dataclass(frozen=True)
class Clip:
    path: Path
    start: float
    end: float


def run(command: list[str], timeout: int = 120) -> str:
    try:
        return subprocess.run(command, check=True, capture_output=True,
                              text=True, timeout=timeout).stdout
    except FileNotFoundError as exc:
        raise MediaError("找不到 FFmpeg / ffprobe，请先安装系统依赖。") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaError("视频处理超时，请缩短片段或减少素材后重试。") from exc
    except subprocess.CalledProcessError as exc:
        raise MediaError("视频无法解析或编码，请确认素材可以正常播放，并尝试转换为标准 MP4。") from exc


def check_tools() -> None:
    if not all(shutil.which(tool) for tool in ("ffmpeg", "ffprobe")):
        raise MediaError("需要安装 FFmpeg（包含 ffprobe）。")


def probe(path: Path) -> MediaInfo:
    data = json.loads(run(["ffprobe", "-v", "error", "-show_format", "-show_streams",
                           "-of", "json", str(path)], timeout=30))
    streams = data.get("streams", [])
    if not any(s.get("codec_type") == "video" for s in streams):
        raise MediaError("素材中没有视频轨道。")
    try:
        duration = float(data["format"]["duration"])
    except (KeyError, TypeError, ValueError) as exc:
        raise MediaError("无法读取视频时长。") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise MediaError("视频时长无效。")
    return MediaInfo(duration, any(s.get("codec_type") == "audio" for s in streams))


def _export_video(clips: list[Clip], ratio: str) -> bytes:
    check_tools()
    if not clips or len(clips) > 120:
        raise MediaError("请选择 1–120 段剪辑片段。")
    if ratio not in ("9:16", "16:9"):
        raise MediaError("请选择支持的输出比例。")
    infos = [probe(c.path) for c in clips]
    for clip, info in zip(clips, infos):
        if not all(math.isfinite(t) for t in (clip.start, clip.end)) or not (
            0 <= clip.start < clip.end <= info.duration
        ):
            raise MediaError("时间范围无效：请确保 0 ≤ 开始时间 < 结束时间 ≤ 视频时长。")
    if sum(c.end - c.start for c in clips) > 600:
        raise MediaError("第一版支持总计最多 10 分钟的成片。")
    width, height = (720, 1280) if ratio == "9:16" else (1280, 720)
    with tempfile.TemporaryDirectory(prefix="vlog-render-") as directory:
        root = Path(directory)
        for i, (clip, info) in enumerate(zip(clips, infos)):
            duration = clip.end - clip.start
            command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                       "-threads", "2", "-ss", str(clip.start), "-i", str(clip.path)]
            if not info.has_audio:
                command += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
            command += ["-t", str(duration), "-map", "0:v:0", "-map",
                        "0:a:0" if info.has_audio else "1:a:0",
                        "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p",
                        "-af", "aresample=48000,apad", "-c:v", "libx264", "-preset", "veryfast",
                        "-crf", "23", "-threads", "2", "-filter_threads", "1", "-c:a", "aac",
                        "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(root / f"{i}.mp4")]
            run(command, timeout=900)
        manifest = root / "clips.txt"
        manifest.write_text("".join(f"file '{i}.mp4'\n" for i in range(len(clips))))
        output = root / "output.mp4"
        run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f",
             "concat", "-safe", "1", "-i", str(manifest), "-c", "copy", "-movflags",
             "+faststart", str(output)], timeout=120)
        return output.read_bytes()


def export_video(clips: list[Clip], ratio: str) -> bytes:
    from vlog_editor.resources import bounded_job
    return bounded_job(_export_video)(clips, ratio)
