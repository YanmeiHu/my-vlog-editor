"""Validated SRT editing and ASS rendering without user-supplied ASS commands."""
from dataclasses import dataclass
import math
import re

from vlog_editor.media import MediaError


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    text: str


def validate(cues: list[Cue], duration: float) -> None:
    if len(cues) > 500:
        raise MediaError("字幕最多支持 500 条。")
    previous = 0.0
    for cue in cues:
        if (not all(math.isfinite(t) for t in (cue.start, cue.end))
                or not 0 <= cue.start < cue.end <= duration + .05
                or cue.start < previous or not cue.text.strip() or len(cue.text) > 2000):
            raise MediaError("字幕时间无效、重叠或超出成片范围，或字幕文字为空/过长。")
        previous = cue.end


def _stamp(seconds: float) -> str:
    total = round(seconds * 1000)
    hours, remainder = divmod(total, 3600000)
    minutes, remainder = divmod(remainder, 60000)
    sec, ms = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{sec:02},{ms:03}"


def to_srt(cues: list[Cue]) -> str:
    return "\n\n".join(f"{i}\n{_stamp(c.start)} --> {_stamp(c.end)}\n{c.text}"
                         for i, c in enumerate(cues, 1)) + ("\n" if cues else "")


def parse_srt(text: str, duration: float) -> list[Cue]:
    if len(text) > 100000:
        raise MediaError("字幕文件过大。")
    cues = []
    def seconds(stamp):
        h, m, s, ms = map(int, re.split('[:,]', stamp))
        if m >= 60 or s >= 60:
            raise MediaError("字幕时间格式无效。")
        return h * 3600 + m * 60 + s + ms / 1000
    for block in re.split(r'\n\s*\n', text.replace('\r\n', '\n').strip()):
        if not block.strip():
            continue
        lines = block.splitlines()
        if len(lines) < 3 or not lines[0].isdigit():
            raise MediaError("请按 SRT 格式填写序号、时间轴和文字。")
        match = re.fullmatch(r'(\d{2,}:\d{2}:\d{2},\d{3}) --> (\d{2,}:\d{2}:\d{2},\d{3})', lines[1])
        if not match:
            raise MediaError("字幕时间格式应为 00:00:00,000 --> 00:00:01,000。")
        cues.append(Cue(seconds(match[1]), seconds(match[2]), '\n'.join(lines[2:])))
    validate(cues, duration)
    return cues


def to_ass(cues: list[Cue]) -> str:
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 720
PlayResY: 1280
WrapStyle: 0
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Noto Sans CJK SC,38,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,1,2,35,35,80,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    def stamp(t):
        total = round(t * 100)
        hours, rest = divmod(total, 360000)
        minutes, rest = divmod(rest, 6000)
        sec, cs = divmod(rest, 100)
        return f"{hours}:{minutes:02}:{sec:02}.{cs:02}"
    for cue in cues:
        text = cue.text.replace('\\', '＼').replace('{', '｛').replace('}', '｝').replace('\n', r'\N')
        header += f"Dialogue: 0,{stamp(cue.start)},{stamp(cue.end)},Default,,0,0,0,,{text}\n"
    return header
