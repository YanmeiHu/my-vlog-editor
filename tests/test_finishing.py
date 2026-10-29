import json
from pathlib import Path
import subprocess
import tempfile

import pytest

from vlog_editor.captions import Cue, parse_srt, to_ass, to_srt
from vlog_editor.finishing import finish_video, transcribe
from vlog_editor.media import Clip, MediaError, export_video, probe
from test_media import make_video


def test_srt_roundtrip_and_escaping():
    cues = [Cue(0, 1.2, '你好 Hello\n第二行')]
    assert parse_srt(to_srt(cues), 2) == cues
    assert r'{\pos' not in to_ass([Cue(0, 1, r'{\pos(0,0)}test')])


@pytest.mark.parametrize('text', ['broken', '1\n00:99:00,000 --> 00:99:01,000\nx',
    '1\n00:00:02,000 --> 00:00:01,000\nx',
    '1\n00:00:00,000 --> 00:00:04,000\nx',
    '1\n00:00:00,000 --> 00:00:01,500\nx\n\n2\n00:00:01,000 --> 00:00:02,000\ny'])
def test_invalid_srt(text):
    with pytest.raises(MediaError):
        parse_srt(text, 2)


@pytest.fixture
def base(tmp_path):
    path = tmp_path / 'input.mp4'
    make_video(path, 'black', True)
    return export_video([Clip(path, 0, .9)], '9:16')


def test_real_burn_and_music(base, tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    music = tmp_path / 'music.wav'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                    'sine=frequency=880:duration=0.2', str(music)], check=True)
    result = finish_video(base, [Cue(.05, .8, '你好 Hello')], music, .3, .5)
    path = tmp_path / 'finished.mp4'
    path.write_bytes(result)
    assert probe(path).has_audio
    assert probe(path).duration == pytest.approx(.9, abs=.12)
    # Verify actual burned text pixels differ from the original black image.
    def frame(video):
        return subprocess.check_output(['ffmpeg', '-v', 'error', '-ss', '0.3', '-i', str(video),
                                        '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'gray', '-'])
    original = tmp_path / 'original.mp4'
    original.write_bytes(base)
    assert sum(frame(path)) > sum(frame(original)) + 10000
    # Verify music frequency is mixed, rather than merely checking an audio track exists.
    import numpy as np
    pcm = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(path), '-vn',
                                   '-ar', '8000', '-ac', '1', '-f', 'f32le', '-'])
    wave = np.frombuffer(pcm, dtype='<f4')
    spectrum = abs(np.fft.rfft(wave))
    frequencies = np.fft.rfftfreq(len(wave), 1/8000)
    assert max(spectrum[abs(frequencies-880) < 10]) > 5
    assert max(spectrum[abs(frequencies-440) < 10]) > 5
    assert not list(tmp_path.glob('vlog-finish-*'))


def test_finishing_failures_cleanup(base, tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    with pytest.raises(MediaError):
        finish_video(base, [], original_volume=float('nan'))
    invalid = tmp_path / 'bad.mp3'
    invalid.write_text('not music')
    with pytest.raises(MediaError):
        finish_video(base, [], invalid)
    assert not list(tmp_path.glob('vlog-finish-*'))


def test_asr_subprocess_contract_and_cleanup(base, tmp_path, monkeypatch):
    import vlog_editor.finishing as finishing
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    original = subprocess.run
    def worker(command, **kwargs):
        if 'vlog_editor.asr_worker' in command:
            assert kwargs['timeout'] == 300
            assert command[-2] == 'zh'
            Path(command[-1]).write_text(json.dumps([{'start': 0, 'end': .5, 'text': '测试'}]))
            return subprocess.CompletedProcess(command, 0)
        return original(command, **kwargs)
    monkeypatch.setattr(finishing.subprocess, 'run', worker)
    assert transcribe(base, 'zh') == [Cue(0, .5, '测试')]
    assert not list(tmp_path.glob('vlog-asr-*'))
    def failure(command, **kwargs):
        if 'vlog_editor.asr_worker' in command:
            raise subprocess.TimeoutExpired(command, 300)
        return original(command, **kwargs)
    monkeypatch.setattr(finishing.subprocess, 'run', failure)
    with pytest.raises(MediaError, match='5 分钟'):
        transcribe(base, 'en')
    assert not list(tmp_path.glob('vlog-asr-*'))
    assert not finishing._ASR_LOCK.locked()


def test_app_auto_and_subtitle_flow(tmp_path, monkeypatch):
    import io
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    path = tmp_path / 'red.mp4'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i',
                    'testsrc2=s=256x256:r=12:d=1','-c:v','libx264','-threads','1',str(path)],check=True)
    upload = io.BytesIO(path.read_bytes())
    upload.name, upload.size = path.name, path.stat().st_size
    monkeypatch.setattr(st, 'file_uploader', lambda label, **kwargs: None if '背景音乐' in label else [upload])
    app = AppTest.from_file('app.py').run()
    app.selectbox[0].set_value('自动选段').run()
    app.button[-1].click().run(timeout=30)
    assert not app.exception
    assert app.session_state['result']
    assert app.warning  # Honest short-source warning.
    app.run()
    assert app.session_state['result']  # First one-click result survives a rerun.
    next(c for c in app.checkbox if c.label == '启用字幕与配乐编辑').check().run()
    app.text_area[0].set_value('1\n00:00:00,050 --> 00:00:00,800\n你好 Hello').run()
    app.button[-1].click().run(timeout=30)
    assert not app.exception
    assert app.session_state['finished']
    assert len(app.get('video')) == 2
    app.text_area[0].set_value('1\n00:00:00,050 --> 00:00:00,800\n修改字幕').run()
    assert 'finished' not in app.session_state


def test_resource_guard_releases_on_failure():
    from vlog_editor.resources import _JOB, bounded_job
    @bounded_job
    def fail():
        raise MediaError('failed')
    with pytest.raises(MediaError):
        fail()
    assert not _JOB.locked()
    _JOB.acquire()
    try:
        with pytest.raises(MediaError, match='其他视频任务'):
            fail()
    finally:
        _JOB.release()
