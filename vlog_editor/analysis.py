"""Explainable scene/motion/silence heuristics, NOT semantic AI understanding."""
from dataclasses import dataclass
import math
from pathlib import Path
import re
import subprocess

from vlog_editor.media import Clip, MediaError, probe
from vlog_editor.resources import bounded_job


@dataclass(frozen=True)
class Candidate:
    source: int
    start: float
    end: float
    score: float
    reason: str


def _log(args: list[str]) -> str:
    try:
        result = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-threads", "1"] + args,
                                capture_output=True, text=True, check=True, timeout=180)
        return result.stderr
    except (subprocess.SubprocessError, FileNotFoundError) as exc:
        raise MediaError("素材分析失败或超时，请使用更短的视频，或切换手动剪辑。") from exc


def parse_changes(log: str) -> list[tuple[float, float]]:
    changes = []
    timestamp = 0.0
    for line in log.splitlines():
        match = re.search(r"pts_time:([\d.]+)", line)
        if match:
            timestamp = float(match[1])
        match = re.search(r"lavfi.scene_score=([\d.]+)", line)
        if match:
            changes.append((timestamp, float(match[1])))
    return changes


def parse_silences(log: str, duration: float) -> list[tuple[float, float]]:
    intervals = []
    start = None
    for kind, value in re.findall(r"silence_(start|end):\s*([\d.]+)", log):
        if kind == "start":
            start = float(value)
        elif start is not None:
            intervals.append((start, min(float(value), duration)))
            start = None
    if start is not None:
        intervals.append((start, duration))
    return intervals


@bounded_job
def analyze(paths: list[Path], segment_seconds: float = 5.0) -> list[Candidate]:
    if not math.isfinite(segment_seconds) or not 2 <= segment_seconds <= 10:
        raise MediaError("候选片段长度应为 2–10 秒。")
    infos = [probe(path) for path in paths]
    if not paths or len(paths) > 12 or sum(info.duration for info in infos) > 600:
        raise MediaError("自动分析最多支持 12 个视频、总素材时长 10 分钟。")
    candidates = []
    for source, (path, info) in enumerate(zip(paths, infos)):
        changes = parse_changes(_log([
            "-i", str(path), "-an", "-vf",
            "fps=3,scale=160:-2,select='gte(scene,0)',metadata=print",
            "-filter_threads", "1", "-f", "null", "-",
        ]))
        silence = []
        if info.has_audio:
            silence = parse_silences(_log([
                "-i", str(path), "-vn", "-af", "silencedetect=noise=-35dB:d=0.5",
                "-f", "null", "-",
            ]), info.duration)
        cuts = [0.0] + [t for t, score in changes if score >= .35 and 0 < t < info.duration] + [info.duration]
        for left, right in zip(cuts, cuts[1:]):
            start = left
            while start < right - .04:
                end = min(start + segment_seconds, right)
                motion = sum(score for t, score in changes if start <= t < end) / max(1, (end-start)*3)
                quiet = sum(max(0, min(end, b)-max(start, a)) for a, b in silence) / (end-start)
                score = motion + (0.1 if info.has_audio else 0) - quiet * .5
                reason = ("明显静音" if quiet >= .5 else "有声片段" if info.has_audio else "无音轨")
                reason += f" · 画面变化 {motion:.3f}"
                candidates.append(Candidate(source, start, end, score, reason))
                start = end
    return candidates


def select_clips(candidates: list[Candidate], paths: list[Path], target: int) -> list[Clip]:
    if target not in (15, 30, 60):
        raise MediaError("请选择 15、30 或 60 秒。")
    chosen = []
    remaining = float(target)
    for candidate in sorted(candidates, key=lambda c: (-c.score, c.source, c.start)):
        if remaining < .04:
            break
        duration = min(remaining, candidate.end - candidate.start)
        if duration < .04:
            continue
        chosen.append((candidate.source, candidate.start, candidate.start + duration))
        remaining -= duration
    # Preserve narrative order after ranking, without repeats or synthetic padding.
    return [Clip(paths[source], start, end) for source, start, end in sorted(chosen)]
