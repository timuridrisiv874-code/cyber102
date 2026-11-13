"""Convert the author-provided Steelcrack masks to RGB detection boxes."""
import argparse
from collections import Counter
import json
from io import BytesIO
from pathlib import Path
import hashlib
import random
from zipfile import ZipFile
from PIL import Image

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--class-id', type=int, required=True)
    parser.add_argument('--train', type=int, default=650)
    parser.add_argument('--val', type=int, default=100)
    parser.add_argument('--test', type=int, default=80)
    args = parser.parse_args()
    if args.class_id < 0 or min(args.train, args.val, args.test) < 1:
        parser.error('Use a nonnegative class ID and positive split sizes')
    root = args.output
    root.mkdir(parents=True, exist_ok=False)
    seen={}
    counts=Counter()
    with ZipFile(args.archive) as archive:
        for source,part,cap in [('Test','test',args.test),('Validation','val',args.val),('Train','train',args.train)]:
            files=sorted(name for name in archive.namelist() if name.startswith(source+'/images/') and name.endswith('.png'))
            random.Random(102).shuffle(files)
            for name in files:
                if counts[part]>=cap: break
                raw=archive.read(name)
                sha=hashlib.sha256(raw).hexdigest()
                if sha in seen: continue
                seen[sha]=part
                with Image.open(BytesIO(raw)) as image, Image.open(BytesIO(archive.read(name.replace('/images/','/masks/')))) as mask:
                    assert image.size==mask.size
                    w,h=image.size
                    bbox=mask.convert('L').point(lambda i:255 if i >= 128 else 0).getbbox()
                    if not bbox:
                        continue
                    x,y,right,bottom=bbox
                    if (right-x)*(bottom-y)>w*h*.94 or min(right-x,bottom-y)<3:
                        continue
                    for folder in ('images','labels'):
                        (root/folder/part).mkdir(parents=True,exist_ok=True)
                    filename='steelcrack-'+Path(name).stem
                    image.convert('RGB').save(root/'images'/part/(filename+'.jpg'),quality=88)
                    center=(x+right)/(2*w),(y+bottom)/(2*h),(right-x)/w,(bottom-y)/h
                    (root/'labels'/part/(filename+'.txt')).write_text(f'{args.class_id} '+' '.join(f'{c:.6f}' for c in center)+'\n')
                    counts[part]+=1
    (root / "steelcrack_counts.json").write_text(json.dumps(counts, indent=2) + "\n")
    print(dict(counts))


if __name__ == '__main__':
    main()
