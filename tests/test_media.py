import json
from pathlib import Path
import subprocess
import tempfile

import pytest
from streamlit.testing.v1 import AppTest

from vlog_editor.media import Clip, MediaError, export_video, probe


def make_video(path, color, audio=False):
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
           f"color=c={color}:s=160x120:r=30:d=1"]
    if audio:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440:duration=1"]
    cmd += ["-c:v", "libx264", "-threads", "1", "-pix_fmt", "yuv420p"]
    if audio:
        cmd += ["-c:a", "aac"]
    subprocess.run(cmd + [str(path)], check=True, capture_output=True)


@pytest.fixture
def sources(tmp_path):
    red, blue = tmp_path / "red.mov", tmp_path / "blue.mp4"
    make_video(red, "red", True)
    make_video(blue, "blue")
    return red, blue


@pytest.mark.parametrize("ratio,size", [("9:16", (720, 1280)), ("16:9", (1280, 720))])
def test_real_export(sources, tmp_path, ratio, size):
    red, blue = sources
    output = tmp_path / "result.mp4"
    # Deliberately reverse input order and trim both clips.
    output.write_bytes(export_video([Clip(blue, .2, .7), Clip(red, .1, .6)], ratio))
    info = probe(output)
    assert info.has_audio
    assert .95 <= info.duration <= 1.2
    data = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(output)]))
    video = next(s for s in data["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == size
    assert video["codec_name"] == "h264"
    for time, channel in [("0.1", 2), ("0.8", 0)]:
        pixel = subprocess.check_output(["ffmpeg", "-v", "error", "-ss", time, "-i", str(output),
                                        "-frames:v", "1", "-vf", "crop=2:2:iw/2:ih/2,scale=1:1",
                                        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
        assert pixel[channel] > 180
        assert pixel[channel] > pixel[(channel + 1) % 3] + 100


@pytest.mark.parametrize("start,end", [(-1, .5), (.5, .5), (0, 2), (float("nan"), .5)])
def test_invalid_trim(sources, start, end):
    with pytest.raises(MediaError, match="时间范围"):
        export_video([Clip(sources[0], start, end)], "9:16")


def test_invalid_media(tmp_path):
    path = tmp_path / "broken.mp4"
    path.write_bytes(b"not a video")
    with pytest.raises(MediaError):
        probe(path)
    with pytest.raises(MediaError):
        export_video([], "9:16")


def test_temp_cleanup(sources, tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    export_video([Clip(sources[0], 0, .2)], "16:9")
    assert not list(tmp_path.glob("vlog-render-*"))
    import vlog_editor.media as media
    original = media.run
    def fail_encoding(command, timeout=120):
        if command[0] == "ffmpeg":
            raise MediaError("模拟编码失败")
        return original(command, timeout)
    monkeypatch.setattr(media, "run", fail_encoding)
    with pytest.raises(MediaError):
        export_video([Clip(sources[0], 0, .2)], "16:9")
    assert not list(tmp_path.glob("vlog-render-*"))


def test_app_empty_state():
    app = AppTest.from_file("app.py").run()
    assert not app.exception
    assert "轻剪" in app.title[0].value
    assert app.get("file_uploader")
    app.button[0].click().run()
    assert not app.exception


def test_app_edit_export_and_invalidation(sources, monkeypatch):
    import io
    import streamlit as st
    uploads = []
    for path in sources:
        upload = io.BytesIO(path.read_bytes())
        upload.name = path.name
        upload.size = len(upload.getvalue())
        uploads.append(upload)
    monkeypatch.setattr(st, "file_uploader", lambda *args, **kwargs: uploads)
    app = AppTest.from_file("app.py").run()
    assert not app.exception
    app.button(key="down-0").click().run()
    assert app.session_state["order"] == [1, 0]
    app.number_input[0].set_value(.1)
    app.number_input[1].set_value(.6)
    app.button[-1].click().run(timeout=30)
    assert not app.exception
    assert app.session_state["result"][4:8] == b"ftyp"
    assert app.get("video")
    assert app.get("download_button")
    app.radio[0].set_value("16:9").run()
    assert "result" not in app.session_state
    app.number_input[1].set_value(0.0)
    app.button[-1].click().run()
    assert app.error
    assert not app.exception


@pytest.mark.parametrize("sizes", [[51], [40, 40, 21], [1] * 13])
def test_app_rejects_upload_limits_before_reading(sizes, monkeypatch):
    from types import SimpleNamespace
    import streamlit as st
    # Metadata alone is enough: validation must happen before reading file buffers.
    uploads = [SimpleNamespace(size=size * 1024**2) for size in sizes]
    monkeypatch.setattr(st, "file_uploader", lambda *args, **kwargs: uploads)
    app = AppTest.from_file("app.py")
    app.session_state["result"] = b"old export"
    app.run()
    assert not app.exception
    assert "素材超过限制" in app.error[0].value
    assert "result" not in app.session_state


def test_app_input_cleanup_on_bad_media(tmp_path, monkeypatch):
    import io
    import streamlit as st
    upload = io.BytesIO(b"broken video")
    upload.name = "broken.mov"
    upload.size = len(upload.getvalue())
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(st, "file_uploader", lambda *args, **kwargs: [upload])
    app = AppTest.from_file("app.py").run()
    assert app.error
    assert not app.exception
    assert not list(tmp_path.glob("vlog-input-*"))


def test_app_input_cleanup_after_export(sources, tmp_path, monkeypatch):
    import io
    import streamlit as st
    upload = io.BytesIO(sources[0].read_bytes())
    upload.name = "source.mov"
    upload.size = len(upload.getvalue())
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(st, "file_uploader", lambda *args, **kwargs: [upload])
    app = AppTest.from_file("app.py").run()
    assert not list(tmp_path.glob("vlog-input-*"))
    app.button[-1].click().run(timeout=30)
    assert not app.exception
    assert app.session_state["result"]
    assert not list(tmp_path.glob("vlog-input-*"))
    assert not list(tmp_path.glob("vlog-render-*"))
