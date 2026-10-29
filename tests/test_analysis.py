from pathlib import Path
import subprocess

import pytest
from streamlit.testing.v1 import AppTest

from vlog_editor.analysis import Candidate, analyze, parse_changes, parse_silences, select_clips
from vlog_editor.media import MediaError, export_video, probe


def test_log_parsing():
    assert parse_changes('frame:1 pts:3 pts_time:1.5\nlavfi.scene_score=0.75') == [(1.5, .75)]
    assert parse_silences('silence_start: 0\nsilence_end: 2\nsilence_start: 4', 6) == [(0, 2), (4, 6)]


def test_real_scene_and_silence(tmp_path):
    path = tmp_path / 'scene.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                    'color=red:s=160x120:r=12:d=3', '-f', 'lavfi', '-i',
                    'color=blue:s=160x120:r=12:d=3', '-f', 'lavfi', '-i',
                    'anullsrc=r=48000:cl=stereo', '-filter_complex',
                    '[0:v][1:v]concat=n=2:v=1:a=0[v]', '-map', '[v]', '-map', '2:a',
                    '-t', '6', '-c:v', 'libx264', '-threads', '1', '-c:a', 'aac', str(path)],
                   check=True, capture_output=True)
    candidates = analyze([path], 5)
    assert any(abs(c.start - 3) < .4 for c in candidates)
    assert all('明显静音' in c.reason for c in candidates)
    clips = select_clips(candidates, [path], 15)
    assert sum(c.end-c.start for c in clips) == pytest.approx(6)
    output = tmp_path / 'out.mp4'
    output.write_bytes(export_video(clips, '16:9'))
    assert probe(output).duration == pytest.approx(6, abs=.15)


@pytest.mark.parametrize('target', [15, 30, 60])
def test_selection_duration_and_order(target):
    candidates = [Candidate(0, i, i+5, float(i), '') for i in range(0, 80, 5)]
    clips = select_clips(candidates, [Path('input.mp4')], target)
    assert sum(c.end-c.start for c in clips) == target
    assert clips == sorted(clips, key=lambda c: c.start)
    assert all(a.end <= b.start for a,b in zip(clips, clips[1:]))


def test_invalid_analysis():
    with pytest.raises(MediaError):
        analyze([], 5)
    with pytest.raises(MediaError):
        analyze([], float('nan'))
