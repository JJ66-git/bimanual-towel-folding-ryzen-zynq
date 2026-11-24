"""Event-driven B35 cooperative UI. Only the LCD worker writes the display IP."""
import hashlib,json,threading,time
from pathlib import Path
from screen.lcd import Framebuffer
from screen.cooperative_state import CooperativeState
from screen.cooperative_view import render,PALETTE

class FoldDisplay:
 def __init__(self,ip,directory,source):
  self.model=CooperativeState(source)
  self.display=Framebuffer(ip,palette=PALETTE,initial_image=render(self.model.snapshot()));self.directory=Path(directory);self.source=source
  self.lock=threading.Lock();self.stop=threading.Event();self.error=None
  self.generation=0
  self.worker=threading.Thread(target=self.paint,daemon=True);self.worker.start()
 def update(self,**fields):
  with self.lock:
   if all(key in fields for key in ('phase','verify','reason','stable','event','cycles')):
    self.model.pl(**fields)
   else:self.model.update(**fields)
 def accepted(self,request,reply,at,service_us):
  with self.lock:self.model.accepted(request,reply,at,service_us)
 def host_claim(self,values,at,session=None):
  with self.lock:self.model.host_claim(values,at,session)
 def authority_claim(self,values,at,session):
  with self.lock:self.model.authority_claim(values,at,session)
 def rejected(self,reason,at):
  with self.lock:self.model.rejected(reason,at)
 def paint(self):
  try:
   while not self.stop.is_set():
    begin=time.monotonic()
    with self.lock:state=self.model.snapshot()
    image=render(state);frame=self.display.present(image);self.generation+=1
    # Atomic outputs keep browser readers from observing partially written data.
    temporary=self.directory/'lcd_latest.tmp.png';image.save(temporary);state['image_sha256']=hashlib.sha256(temporary.read_bytes()).hexdigest();temporary.replace(self.directory/'lcd_latest.png')
    state.update(display_generation=self.generation,framebuffer=frame,physical_confirmation_record='../maintenance/lcd_visual_confirmation.json')
    temporary=self.directory/'lcd_latest.tmp.json';temporary.write_text(json.dumps(state,ensure_ascii=False,separators=(',',':'))+'\n');temporary.replace(self.directory/'lcd_latest.json')
    self.stop.wait(max(.01,.5-(time.monotonic()-begin)))
  except Exception as error:self.error=repr(error)
 def close(self):
  self.stop.set();self.worker.join(timeout=3);self.display.close(preserve=True)
