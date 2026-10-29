"""Run only in a disposable subprocess; optional model never stays in web RAM."""
import json
import os
from pathlib import Path
import sys
import tempfile


def main():
    from faster_whisper import WhisperModel
    # Multilingual tiny, CPU int8; a local converted model path avoids downloads.
    model = WhisperModel(os.environ.get('VLOG_WHISPER_MODEL', 'tiny'), device='cpu',
                         compute_type='int8', cpu_threads=2, num_workers=1,
                         download_root=os.environ.get('VLOG_MODEL_CACHE',
                             str(Path(tempfile.gettempdir()) / 'vlog-whisper-models')))
    segments, _ = model.transcribe(sys.argv[1], language=sys.argv[2], beam_size=1,
                                  condition_on_previous_text=False, vad_filter=False)
    Path(sys.argv[3]).write_text(json.dumps([
        {'start': s.start, 'end': s.end, 'text': s.text} for s in segments
    ], ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    main()
