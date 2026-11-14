"""Report comparable smoke/flame statistics on the original D-Fire split."""
import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import torch
from ultralytics import YOLO, settings
import yaml

from prepare_dfire import CLASSES


def measure(weights, dataset):
    settings.update({'sync': False})
    torch.set_num_threads(4)
    model = YOLO(str(weights), task='detect')
    if tuple(model.names.get(index) for index in range(len(CLASSES))) != CLASSES:
        raise ValueError('Checkpoint IDs do not match the D-Fire classes')
    config = yaml.safe_load(dataset.read_text(encoding='utf-8'))
    config['names'] = dict(model.names)
    with TemporaryDirectory() as folder:
        path = Path(folder)
        data = path / 'config.yaml'
        data.write_text(yaml.safe_dump(config), encoding='utf-8')
        result = model.val(data=str(data), split='test', device='cpu', imgsz=768, batch=6, workers=0,
                           plots=False, project=str(path), verbose=False, amp=False)
    return {model.names[int(i)]: dict(zip(('precision', 'recall', 'ap50', 'ap50to95'), result.class_result(k)))
            for k, i in enumerate(result.ap_class_index) if model.names[int(i)] in CLASSES}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('weights', type=Path)
    parser.add_argument('dataset', type=Path)
    parser.add_argument('output', type=Path)
    opts = parser.parse_args()
    opts.output.write_text(json.dumps(measure(opts.weights, opts.dataset), indent=2) + '\n', encoding='utf-8')
