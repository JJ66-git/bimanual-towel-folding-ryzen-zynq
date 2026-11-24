"""Rebuild the licensed Chinese UI font subsets on the PC (fonttools/brotli required)."""
from pathlib import Path
from fontTools.ttLib import TTFont
from fontTools import subset
ROOT=Path(__file__).resolve().parents[2]
def main():
 screen=ROOT/'fpga_integration/fpga_code/fpga_workspace/screen';web=ROOT/'npu_deploy/fpga_board/web'
 paths=list(screen.glob('cooperative_*.py'))+list(web.glob('*.html'))+list(web.glob('*.js'))
 paths+=[screen.parent/'board/booth_display.py',screen.parent/'host/action_status.py']
 text=''.join(p.read_text() for p in paths)+''.join(chr(i) for i in range(32,127))+'…未知数据'
 for suffix,style in [('', 'Regular'),('-Bold','Bold')]:
  font=TTFont('/usr/share/fonts/opentype/noto/NotoSansCJK-'+style+'.ttc',fontNumber=2)
  opts=subset.Options();opts.layout_features=['*'];opts.name_IDs=['*'];sub=subset.Subsetter(options=opts);sub.populate(text=text);sub.subset(font)
  font.save(screen/'assets'/('DisplaySansSC'+suffix+'.otf'));font.flavor='woff2';font.save(web/'assets'/('DisplaySansSC'+suffix+'.woff2'))
if __name__=='__main__':main()
