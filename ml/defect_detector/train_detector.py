from pathlib import Path
import argparse
import hashlib
import json
from collections import Counter
import yaml
from ultralytics import YOLO, settings
import torch

def main():
    p=argparse.ArgumentParser()
    p.add_argument('role',choices=('online','mobile'))
    p.add_argument('--epochs',type=int,default=35)
    p.add_argument('--data',type=Path,default=Path('dataset'))
    p.add_argument('--initial-weights',type=Path)
    p.add_argument('--name')
    args=p.parse_args()
    root=Path.cwd()
    dataset=args.data.resolve()
    coverage = dataset / 'coverage.json'
    catalog = coverage if coverage.is_file() else dataset.parent / 'annotations.json'
    dataset_info = json.loads(catalog.read_text())
    if dataset_info.get('missingTrainLabels'):
        raise ValueError('Missing training examples for ' + ', '.join(dataset_info['missingTrainLabels']))
    classes=dataset_info['classes']
    cfg={'path':str(dataset),'train':'images/train','val':'images/val','names':dict(enumerate(classes))}
    if (dataset/'images/test').is_dir():
        cfg['test']='images/test'
    config=dataset/'config.yaml'
    config.write_text(yaml.safe_dump(cfg),encoding='utf-8')
    print('dataset',dict(Counter(int(line.split()[0]) for f in (dataset/'labels/train').glob('*.txt') for line in f.read_text().splitlines())),flush=True)
    if not torch.cuda.is_available():
        raise RuntimeError('CUDA is required')
    settings.update({'sync':False})
    model_name, resolution, batch = ('yolo11m.pt',768,6) if args.role=='online' else ('yolo11n.pt',416,6)
    initial_weights = (args.initial_weights or root/model_name).resolve(strict=True)
    run_name = args.name or args.role
    model=YOLO(str(initial_weights),task='detect')
    model.train(data=str(config),imgsz=resolution,epochs=args.epochs,patience=15,batch=batch,workers=0,
                      device=0,project=str(root/'runs'),name=run_name,exist_ok=False,seed=102,
                      plots=False,cache=False,amp=False,mosaic=.65,fliplr=.5,close_mosaic=5)
    checkpoint=Path(model.trainer.best)
    trained=YOLO(str(checkpoint),task='detect')
    exported=Path(trained.export(format='onnx',imgsz=resolution,opset=17,dynamic=False,simplify=False,device='cpu'))
    torch.cuda.empty_cache()
    catalog = Path(__file__).with_name('classes.json')
    supported_labels = {row['id'] for row in json.loads(catalog.read_text(encoding='utf-8'))['labels']}
    manifest={'schemaVersion':1,'model':'team-detector','modelVersion':'cyber102-'+run_name,
              'weightsSha256':hashlib.sha256(checkpoint.read_bytes()).hexdigest(),'imageSize':resolution,'minimumScore':.25,
              'classMap':{str(i):{'sourceName':label,'class':label if label in supported_labels else 'visible_damage',
                                  'component':'unknown'} for i,label in enumerate(classes)}}
    (checkpoint.parent/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print('ARTIFACTS',checkpoint,exported,flush=True)
if __name__=='__main__':
    main()
