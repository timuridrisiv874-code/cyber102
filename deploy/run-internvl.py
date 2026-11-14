"""Scheduled-task wrapper for the selected isolated local VLM worker."""
import os
from pathlib import Path
import runpy
import sys
import traceback

root = Path(__file__).resolve().parent.parent
os.chdir(root)
sys.path.insert(0, str(root))
(root / 'var').mkdir(exist_ok=True)
with (root / 'var' / 'internvl-worker.log').open('a', encoding='utf-8', buffering=1) as log:
    sys.stdout = log
    sys.stderr = log
    try:
        from server.vision_models import configure_from_file
        selected_model = configure_from_file(root)
        sys.argv = ['vision_runtime.worker', '--model', selected_model]
        runpy.run_module('vision_runtime.worker', run_name='__main__')
    except Exception:
        # The scheduler outlives this process. Record the traceback before its log closes.
        traceback.print_exc(file=log)
        raise
