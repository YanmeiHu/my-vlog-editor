"""Subtitle burn-in, bounded optional offline ASR, and music mixing."""
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

from vlog_editor.captions import Cue, to_ass, validate
from vlog_editor.media import MediaError, probe, run
from vlog_editor.resources import bounded_job

_ASR_LOCK = threading.Lock()


@bounded_job
def transcribe(video: bytes, language: str) -> list[Cue]:
    if language not in ('zh', 'en'):
        raise MediaError("仅支持中文或英文语音识别。")
    if not _ASR_LOCK.acquire(blocking=False):
        raise MediaError("语音识别正在处理其他任务，请稍后再试。")
    try:
        with tempfile.TemporaryDirectory(prefix='vlog-asr-') as directory:
            root = Path(directory)
            source = root / 'source.mp4'
            source.write_bytes(video)
            duration = probe(source).duration
            if duration > 120:
                raise MediaError("免费平台语音识别仅支持不超过 120 秒的成片。")
            audio = root / 'audio.wav'
            run(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-i', str(source), '-vn',
                 '-ar', '16000', '-ac', '1', str(audio)])
            result = root / 'captions.json'
            try:
                subprocess.run([sys.executable, '-m', 'vlog_editor.asr_worker', str(audio),
                                language, str(result)], capture_output=True, text=True,
                               timeout=300, check=True)
            except (subprocess.SubprocessError, OSError) as exc:
                raise MediaError("语音识别失败或超过 5 分钟：请确认已安装 requirements-asr.txt、模型可下载或已配置本地模型，且内存充足。仍可手动填写 SRT 字幕。") from exc
            cues = [Cue(max(0, c['start']), min(duration, c['end']), c['text'].strip())
                    for c in json.loads(result.read_text()) if c['text'].strip() and c['start'] < duration]
            validate(cues, duration)
            return cues
    finally:
        _ASR_LOCK.release()


@bounded_job
def finish_video(video: bytes, cues: list[Cue], music: Path | None = None,
                 music_volume: float = .2, original_volume: float = 1.0) -> bytes:
    if not all(math.isfinite(v) and 0 <= v <= 1 for v in (music_volume, original_volume)):
        raise MediaError("音量应在 0 到 1 之间。")
    with tempfile.TemporaryDirectory(prefix='vlog-finish-') as directory:
        root = Path(directory)
        source = root / 'source.mp4'
        source.write_bytes(video)
        info = probe(source)
        validate(cues, info.duration)
        if not cues and music is None and original_volume == 1:
            return video
        args = ['ffmpeg', '-v', 'error', '-nostdin', '-y', '-threads', '2', '-i', str(source)]
        if music is not None:
            if music.stat().st_size > 20 * 1024**2:
                raise MediaError("背景音乐不得超过 20 MB。")
            data = json.loads(run(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(music)]))
            if not any(s.get('codec_type') == 'audio' for s in data.get('streams', [])):
                raise MediaError("背景音乐文件中没有音轨。")
            args += ['-stream_loop', '-1', '-i', str(music)]
            args += ['-filter_complex',
                     f'[0:a]volume={original_volume}[original];[1:a]volume={music_volume}[music];'
                     '[original][music]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95:latency=1[a]',
                     '-map', '0:v:0', '-map', '[a]']
        else:
            args += ['-map', '0:v:0', '-map', '0:a:0', '-af', f'volume={original_volume}']
        if cues:
            subtitles = root / 'captions.ass'
            subtitles.write_text(to_ass(cues), encoding='utf-8')
            args += ['-vf', f'ass={subtitles}', '-c:v', 'libx264', '-preset', 'veryfast',
                     '-crf', '23', '-pix_fmt', 'yuv420p', '-threads', '2', '-filter_threads', '1']
        else:
            args += ['-c:v', 'copy']
        output = root / 'finished.mp4'
        args += ['-c:a', 'aac', '-t', str(info.duration), '-movflags', '+faststart', str(output)]
        run(args, timeout=900)
        return output.read_bytes()
