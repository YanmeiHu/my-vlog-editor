from pathlib import Path
import subprocess
import tempfile

import cv2
import numpy as np
import pytest

from vlog_editor.visual import (VisualCandidate, analyze_visual, image_features,
                                near_duplicate, plan_clips, residual_motion)
from vlog_editor.beats import detect_beats, energy_beats
from vlog_editor.media import MediaError


def pattern():
    y,x=np.indices((192,192))
    image=(((x//12+y//12)%2)*170+40).astype(np.uint8)
    cv2.putText(image,'VLOG',(15,110),cv2.FONT_HERSHEY_SIMPLEX,1,128,3)
    return image


def fixture_video(tmp_path, blur=False):
    image=pattern()
    if blur:
        image=cv2.GaussianBlur(image,(0,0),6)
    png=tmp_path/('blur.png' if blur else 'sharp.png')
    path=png.with_suffix('.mp4')
    cv2.imwrite(str(png),image)
    subprocess.run(['ffmpeg','-v','error','-y','-loop','1','-i',str(png),'-t','3',
                    '-r','4','-c:v','libx264','-threads','1','-pix_fmt','yuv420p',str(path)],check=True)
    return path


def candidate(source=0,start=0,end=15,score=.5,thumb=.5,hash=1,eligible=True):
    return VisualCandidate(source,start,end,score,'test',1,0,0,100,.8,hash,(thumb,)*64,eligible)


def test_quality_is_measured_from_pixels():
    sharp=pattern()
    blurred=cv2.GaussianBlur(sharp,(0,0),6)
    assert image_features(sharp)[0] > image_features(blurred)[0]*10
    assert image_features(np.zeros((192,192),np.uint8))[1] == 0


def test_motion_compensates_camera_translation():
    image=pattern()
    shifted=cv2.warpAffine(image,np.float32([[1,0,2],[0,1,0]]),(192,192),borderMode=cv2.BORDER_REFLECT)
    motion,_=residual_motion(image,shifted)
    assert motion < .5
    moving=image.copy()
    moving[30:130,30:130]=np.roll(image[30:130,30:130],8,axis=1)
    action,_=residual_motion(image,moving)
    assert action > motion+1


def test_real_quality_duplicate_and_static_filter(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    sharp=fixture_video(tmp_path)
    blur=fixture_video(tmp_path,True)
    candidates=analyze_visual([sharp,blur,sharp],2,'vlog',8)
    assert any(c.eligible for c in candidates if c.source==0)
    assert all(not c.eligible for c in candidates if c.source==1)
    plan=plan_clips(candidates,[sharp,blur,sharp],15,'vlog')
    assert len(plan)==1
    assert plan[0].path==sharp
    assert all(not c.eligible for c in analyze_visual([sharp],2,'sports'))
    assert not list(tmp_path.glob('vlog-visual-*'))


def test_real_motion_selected_for_sports(tmp_path):
    path=tmp_path/'motion.mp4'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i',
                    'testsrc2=s=256x256:r=12:d=3','-c:v','libx264','-threads','1',str(path)],check=True)
    candidates=analyze_visual([path],2,'sports')
    assert any(c.motion>.12 and c.eligible for c in candidates)


def test_quality_ranking_before_chronology():
    candidates=[candidate(source=0,score=.1,thumb=.1),candidate(source=1,score=.9,thumb=.9)]
    assert plan_clips(candidates,[Path('low'),Path('high')],15,'vlog')[0].path==Path('high')
    assert near_duplicate(candidate(),candidate())
    assert not near_duplicate(candidate(thumb=.1),candidate(thumb=.9))
    assert not plan_clips([candidate(eligible=False)],[Path('a')],15)


def test_beat_detection_and_alignment(tmp_path):
    rate=8000
    samples=np.zeros(rate*10,np.float32)
    for t in np.arange(.5,10,.5):
        i=int(t*rate)
        samples[i:i+160]=.8*np.sin(np.arange(160)*2*np.pi*440/rate)
    beats=energy_beats(samples)
    assert len(beats)>=18
    assert max(min(abs(b-t) for b in beats) for t in np.arange(.5,9.6,.5)) < .04
    assert energy_beats(np.zeros(rate,np.float32))==[]
    raw=tmp_path/'music.f32'
    samples.tofile(raw)
    wav=tmp_path/'music.wav'
    subprocess.run(['ffmpeg','-v','error','-y','-f','f32le','-ar','8000','-ac','1','-i',str(raw),str(wav)],check=True)
    assert len(detect_beats(wav))==len(beats)
    pool=[candidate(start=i*4,end=i*4+4,thumb=i*.15,hash=i,score=1-i*.1) for i in range(5)]
    grid=[1.5,3,4.5,6,7.5,9,10.5,12,13.5,15]
    clips=plan_clips(pool,[Path('input')],15,'sports',grid,deduplicate=False)
    cuts=np.cumsum([c.end-c.start for c in clips])
    assert all(any(abs(t-b)<.001 for b in grid) for t in cuts[:-1])


def test_invalid_options():
    with pytest.raises(MediaError):
        analyze_visual([],2,'unknown')


def test_visual_failure_cleans_frames(tmp_path, monkeypatch):
    import vlog_editor.visual as visual
    monkeypatch.setattr(tempfile,'tempdir',str(tmp_path))
    source=fixture_video(tmp_path)
    def fail(_):
        raise MediaError('failure during feature extraction')
    monkeypatch.setattr(visual,'image_features',fail)
    with pytest.raises(MediaError):
        analyze_visual([source],2)
    assert not list(tmp_path.glob('vlog-visual-*'))


def test_mobile_auto_adjust_and_beat_music(tmp_path, monkeypatch):
    import io
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    from vlog_editor.media import probe
    source=tmp_path/'moving.mp4'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i',
                    'testsrc2=s=256x256:r=12:d=3','-c:v','libx264','-threads','1',str(source)],check=True)
    music=tmp_path/'beat.wav'
    samples=np.zeros(8000*3,np.float32)
    for t in [.5,1,1.5,2,2.5]:
        i=int(t*8000)
        samples[i:i+160]=.8*np.sin(np.arange(160)*2*np.pi*440/8000)
    raw=tmp_path/'samples.f32'
    samples.tofile(raw)
    subprocess.run(['ffmpeg','-v','error','-y','-f','f32le','-ar','8000','-ac','1',
                    '-i',str(raw),str(music)],check=True)
    def uploaded(path):
        result=io.BytesIO(path.read_bytes())
        result.name,result.size=path.name,path.stat().st_size
        return result
    video_upload,music_upload=uploaded(source),uploaded(music)
    monkeypatch.setattr(st,'file_uploader',lambda label,**kwargs: music_upload if '背景音乐' in label else [video_upload])
    app=AppTest.from_file('app.py').run()
    app.selectbox[0].set_value('自动选段').run()
    app.selectbox[1].set_value('运动精彩集锦').run()
    next(b for b in app.button if b.label=='分析素材并生成候选').click().run(timeout=30)
    assert not app.exception
    assert app.session_state['beats']
    next(n for n in app.number_input if n.label=='片段结束（秒）').set_value(1.5).run()
    next(b for b in app.button if b.label=='生成我的视频').click().run(timeout=30)
    assert not app.exception
    output=tmp_path/'result.mp4'
    output.write_bytes(app.session_state['result'])
    assert probe(output).duration==pytest.approx(1.5,abs=.15)
    assert probe(output).has_audio
    next(n for n in app.number_input if n.label=='片段结束（秒）').set_value(1.0).run()
    assert 'result' not in app.session_state
