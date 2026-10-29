"""Measured visual features + trained HOG person detection; no semantic event model."""
from dataclasses import dataclass
import json
import math
from pathlib import Path
import tempfile

import cv2
import numpy as np

from vlog_editor.analysis import Candidate, _log, parse_silences
from vlog_editor.media import Clip, MediaError, probe, run
from vlog_editor.resources import bounded_job

cv2.setNumThreads(1)


@dataclass(frozen=True)
class VisualCandidate(Candidate):
    motion: float
    person_motion: float
    people: int
    sharpness: float
    exposure: float
    fingerprint: int
    thumbnail: tuple[float, ...]
    eligible: bool


def image_features(gray: np.ndarray) -> tuple[float, float, int, tuple[float, ...]]:
    sharpness = float(cv2.Laplacian(gray, cv2.CV_32F).var())
    brightness = float(gray.mean())
    exposure = float(np.clip(1 - abs(brightness - 128) / 128, 0, 1))
    small = cv2.resize(gray, (32, 32)).astype(np.float32)
    dct = cv2.dct(small)[:8, :8].flatten()[1:]
    threshold = float(np.median(dct))
    fingerprint = sum(int(value > threshold) << i for i, value in enumerate(dct))
    thumbnail = tuple((cv2.resize(gray, (8, 8)).flatten() / 255).tolist())
    return sharpness, exposure, fingerprint, thumbnail


def residual_motion(previous: np.ndarray, current: np.ndarray,
                    boxes=()) -> tuple[float, float]:
    flow = cv2.calcOpticalFlowFarneback(previous, current, None, .5, 3, 15, 3, 5, 1.2, 0)
    # Remove median camera translation; zoom/rotation still can look like action.
    residual = flow - np.median(flow.reshape(-1, 2), axis=0)
    magnitude = np.linalg.norm(residual, axis=2)
    motion = float(np.percentile(magnitude, 85))
    person = 0.0
    for x, y, width, height in boxes:
        roi = magnitude[max(0,y):min(current.shape[0],y+height), max(0,x):min(current.shape[1],x+width)]
        if roi.size:
            person = max(person, float(np.percentile(roi, 75)))
    return motion, person


def _content_rect(path: Path):
    data = json.loads(run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                          '-show_streams', '-of', 'json', str(path)]))['streams'][0]
    width, height = data['width'], data['height']
    rotation = next((s.get('rotation', 0) for s in data.get('side_data_list', []) if 'rotation' in s), 0)
    if abs(rotation) % 180 == 90:
        width, height = height, width
    scale = min(256/width, 256/height)
    w, h = int(width*scale)//2*2, int(height*scale)//2*2
    return (256-w)//2, (256-h)//2, max(2,w), max(2,h)


@bounded_job
def analyze_visual(paths: list[Path], segment_seconds=5.0, profile='vlog', min_sharpness=8.0):
    if profile not in ('sports', 'vlog') or not math.isfinite(segment_seconds) or not 2 <= segment_seconds <= 10:
        raise MediaError('请选择有效的模式和 2–10 秒候选长度。')
    if not math.isfinite(min_sharpness) or not 0 <= min_sharpness <= 1000:
        raise MediaError('清晰度阈值无效。')
    infos = [probe(p) for p in paths]
    if not paths or len(paths) > 12 or sum(i.duration for i in infos) > 600:
        raise MediaError('自动分析最多支持 12 个素材、总时长 10 分钟。')
    hog = cv2.HOGDescriptor()
    hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
    result = []
    with tempfile.TemporaryDirectory(prefix='vlog-visual-') as directory:
        raw = Path(directory) / 'frames.gray'
        for source, (path, info) in enumerate(zip(paths, infos)):
            run(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-threads', '1', '-i', str(path),
                 '-an', '-vf', 'fps=2,scale=256:256:force_original_aspect_ratio=decrease,pad=256:256:(ow-iw)/2:(oh-ih)/2',
                 '-filter_threads', '1', '-pix_fmt', 'gray', '-f', 'rawvideo', str(raw)], timeout=180)
            x, y, w, h = _content_rect(path)
            rows, previous, previous_small = [], None, None
            boxes = ()
            with raw.open('rb') as handle:
                index = 0
                while chunk := handle.read(256*256):
                    if len(chunk) != 256*256:
                        raise MediaError('分析帧不完整，请尝试其他素材。')
                    frame = np.frombuffer(chunk, np.uint8).reshape(256,256)
                    crop = frame[y+2:y+h-2, x+2:x+w-2]
                    if crop.size < 16:
                        crop = frame
                    sharp, exposure, fingerprint, thumbnail = image_features(crop)
                    small = cv2.resize(frame, (128,128))
                    if index % 2 == 0:
                        detected, _ = hog.detectMultiScale(frame, winStride=(8,8), padding=(8,8), scale=1.1)
                        boxes = tuple(tuple(int(v)//2 for v in box) for box in detected)
                    motion, person = residual_motion(previous_small, small, boxes) if previous_small is not None else (0.,0.)
                    change = float(np.mean(abs(frame.astype(float)-previous.astype(float)))/255) if previous is not None else 0
                    # Avoid interpreting a shot boundary as continuous person/object motion.
                    cut = change > .22
                    if cut:
                        motion, person = 0., 0.
                    rows.append((index/2, sharp, exposure, fingerprint, thumbnail, motion, person, len(boxes), cut))
                    previous, previous_small = frame.copy(), small
                    index += 1
            silence = parse_silences(_log(['-i', str(path), '-vn', '-af',
                      'silencedetect=noise=-35dB:d=0.5', '-f', 'null', '-']), info.duration) if info.has_audio else []
            cuts = [0.] + [row[0] for row in rows if row[-1]] + [info.duration]
            for left, right in zip(cuts, cuts[1:]):
                start = left
                while start < right - .04:
                    end = min(start+segment_seconds, right)
                    samples = [r for r in rows if start <= r[0] < end]
                    if samples:
                        representative = max(samples, key=lambda r:r[1])
                        sharp = float(np.median([r[1] for r in samples]))
                        exposure = float(np.mean([r[2] for r in samples]))
                        motion = float(np.mean([r[5] for r in samples]))
                        person = float(np.mean([r[6] for r in samples]))
                        people = max(r[7] for r in samples)
                        quiet = sum(max(0,min(end,b)-max(start,a)) for a,b in silence)/(end-start)
                        quality = min(1, sharp/150) * .7 + exposure*.3
                        score = (min(motion/2,1)*.45 + min(person/2,1)*.2 + quality*.35 if profile=='sports'
                                 else quality*.8 + min(motion,1)*.15 + (1-quiet)*.05)
                        eligible = sharp >= min_sharpness and exposure >= .08 and (profile!='sports' or motion >= .12 or person >= .12)
                        reason = f'运动 {motion:.2f} · 人物区域运动 {person:.2f} · 人物检测 {people} · 清晰度 {sharp:.1f} · 曝光 {exposure:.2f}'
                        if not eligible:
                            reason += ' · 未通过质量/动作阈值'
                        result.append(VisualCandidate(source,start,end,score,reason,motion,person,people,sharp,exposure,
                                                     representative[3],representative[4],eligible))
                    start = end
            raw.unlink(missing_ok=True)
    return result


def near_duplicate(a: VisualCandidate, b: VisualCandidate) -> bool:
    return ((a.fingerprint ^ b.fingerprint).bit_count() <= 6
            and float(np.mean((np.array(a.thumbnail)-np.array(b.thumbnail))**2)) < .012)


def plan_clips(candidates, paths, target, profile='vlog', beats=(), deduplicate=True):
    if target not in (15,30,60) or profile not in ('vlog','sports'):
        raise MediaError('目标时长或剪辑模式无效。')
    unique = []
    for candidate in sorted(candidates, key=lambda c:(-c.score,c.source,c.start)):
        if not candidate.eligible or (deduplicate and any(near_duplicate(candidate,c) for c in unique)):
            continue
        unique.append(candidate)
    if profile == 'vlog':
        # First choose a quality-ranked pool, then restore its timeline order.
        pool, available = [], 0.0
        for candidate in unique:
            pool.append(candidate)
            available += candidate.end-candidate.start
            if available >= target + (min(target*.25, 10) if beats else 0):
                break
        unique = pool
        unique.sort(key=lambda c:(c.source,c.start))
    clips, elapsed = [], 0.
    for c in unique:
        duration = min(c.end-c.start,target-elapsed)
        if duration < .04:
            break
        # Snap only when a beat fits the available non-overlapping source window.
        possible = [b for b in beats if elapsed+min(1.,duration) <= b <= elapsed+duration+.0001]
        if possible and elapsed+duration < target-.04:
            duration = max(possible)-elapsed
        clips.append(Clip(paths[c.source],c.start,c.start+duration))
        elapsed += duration
        if elapsed >= target-.04:
            break
    return clips
