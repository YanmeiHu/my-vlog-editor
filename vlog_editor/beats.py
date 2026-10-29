"""Audio-energy onset estimates, not musicological beat/downbeat understanding."""
from pathlib import Path
import tempfile

import numpy as np

from vlog_editor.media import MediaError, run
from vlog_editor.resources import bounded_job


def energy_beats(samples: np.ndarray, sample_rate=8000) -> list[float]:
    hop = int(sample_rate*.01)
    count = len(samples)//hop
    if count < 10:
        return []
    rms = np.sqrt(np.mean(samples[:count*hop].reshape(count,hop)**2,axis=1))
    if max(rms) < .005:
        return []
    novelty = np.maximum(0,np.diff(rms,prepend=rms[0]))
    threshold = max(.008,float(np.median(novelty)+3*np.median(abs(novelty-np.median(novelty)))))
    peaks = [i for i in range(1,len(novelty)-1) if novelty[i]>threshold and novelty[i]>=novelty[i-1] and novelty[i]>novelty[i+1]]
    chosen = []
    for i in sorted(peaks,key=lambda i:-novelty[i]):
        if all(abs(i-j) >= 25 for j in chosen):
            chosen.append(i)
    return sorted(round(i*.01,3) for i in chosen)


@bounded_job
def detect_beats(path: Path) -> list[float]:
    if path.stat().st_size > 20*1024**2:
        raise MediaError('背景音乐不得超过 20 MB。')
    with tempfile.TemporaryDirectory(prefix='vlog-beats-') as directory:
        output = Path(directory)/'music.f32'
        run(['ffmpeg','-v','error','-nostdin','-y','-i',str(path),'-t','60','-vn',
             '-ar','8000','-ac','1','-f','f32le',str(output)],timeout=60)
        return energy_beats(np.fromfile(output,dtype='<f4'))
