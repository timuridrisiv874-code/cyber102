"""Scheduled-task wrapper for the isolated local InternVL worker."""
import os
from pathlib import Path
import runpy
import sys

root = Path(__file__).resolve().parent.parent
os.chdir(root)
sys.path.insert(0, str(root))
(root / 'var').mkdir(exist_ok=True)
with (root / 'var' / 'internvl-worker.log').open('a', encoding='utf-8', buffering=1) as log:
    sys.stdout = log
    sys.stderr = log
    sys.argv = ['vision_runtime.worker']
    runpy.run_module('vision_runtime.worker', run_name='__main__')
