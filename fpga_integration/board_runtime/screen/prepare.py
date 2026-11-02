"""Prepare the exact 16-color frame and preview without accessing hardware."""
import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image
from screen.lcd import encode_portrait, decode_native


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True,help='new output directory')
    args = parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=False)
    with Image.open(args.image) as image:
        packed = encode_portrait(image)
    (args.out/'frame.bin').write_bytes(packed)
    native = decode_native(packed)
    native.save(args.out/'native_preview.png')
    native.transpose(Image.ROTATE_270).save(args.out/'portrait_preview.png')
    data = dict(format='LCD1_4BPP',portrait=[480,800],native=[800,480],
                frame_bytes=len(packed),sha256=hashlib.sha256(packed).hexdigest(),
                physical_display_verified=False)
    (args.out/'manifest.json').write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8')
    print('Prepared framebuffer files:',args.out)


if __name__ == '__main__':
    main()
