"""Scheduled-task entry point; append logs without storing request credentials."""
import os
from pathlib import Path
import runpy
import sys

root = Path(__file__).resolve().parent.parent
os.chdir(root)
sys.path.insert(0, str(root))
from server.vision_models import configure_from_file
configure_from_file(root)
backend_file = root / 'var' / 'vision-backend.txt'
backend = backend_file.read_text(encoding='utf-8-sig').strip() if backend_file.exists() else 'internvl3'
if backend not in ('internvl3', 'ollama', 'detector'):
    raise ValueError('Unknown configured local vision backend')
with (root / 'var' / 'server.log').open('a', encoding='utf-8', buffering=1) as log:
    sys.stdout = log
    sys.stderr = log
    sys.argv = ['server.app', '--vision', '--vision-backend', backend, '--bind', '127.0.0.1', '--port', '8102',
                '--public-url', 'https://cyber102.94.41.87.102.sslip.io']
    runpy.run_module('server.app', run_name='__main__')
