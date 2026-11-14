"""Scheduled CPU visual search worker; independent of the GPU model and its queue."""
import os
from pathlib import Path
import runpy
import sys
import traceback

root = Path(__file__).resolve().parent.parent
os.chdir(root)
sys.path.insert(0, str(root))
(root / 'var').mkdir(exist_ok=True)
with (root / 'var' / 'visual-search.log').open('a', encoding='utf-8', buffering=1) as log:
    sys.stdout = log
    sys.stderr = log
    sys.argv = ['vision_runtime.visual_worker', '--threads', '2']
    try:
        runpy.run_module('vision_runtime.visual_worker', run_name='__main__')
    except Exception:
        traceback.print_exc(file=log)
        raise
