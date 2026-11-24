"""Booth-only LCD renderer: results come from matched PL replies."""
import json
import hashlib
from pathlib import Path
import threading
import time


def render(state,font_path=None):
    from PIL import Image,ImageDraw
    from screen.cooperative_view import font,PALETTE,OLIVE
    paper,ink,accent,lime,line,muted,white,dark,green,amber,red,shade,*_=PALETTE
    image=Image.new('RGB',(480,800),paper);draw=ImageDraw.Draw(image)
    def text(x,y,value,size=20,color=ink,max_width=432):
        value=str(value);face=font(size,size>=24)
        while value and face.getmask(value,mode='L').size[0]>max_width:value=value[:-1]
        mask=face.getmask(value,mode='L')
        if mask.size[0] and mask.size[1]:image.paste(color,(x,y),Image.frombytes('L',mask.size,bytes(mask)))
    def panel(box,color):draw.rectangle(box,fill=color)
    mask=state.get('mask');value=state.get('value')
    result_color=muted if mask is None else red if mask else OLIVE
    headline='等待互动操作' if mask is None else '动作目标超范围' if mask&1 else '动作数据有异常' if mask else '本条规则检查通过'
    text(24,24,'FlexiFold',30);panel((335,24,456,58),ink);text(350,32,'互动诊断',20,white,100)
    text(24,69,'历史动作回放 · FPGA 实际检查',18,muted)
    draw.line((24,100,456,100),fill=ink,width=1)
    panel((24,120,456,330),ink)
    text(42,151,headline,36,white,396)
    text(42,213,'请在电脑上拖动滑块或点选示例' if mask is None else '目标超过设定上限' if mask&1 else '本条输入未触发动作规则' if mask==0 else '输入不符合动作规则',22,white,396)
    text(42,280,'此结果不代表折叠任务成功',20,lime,396)
    panel((24,354,456,487),white)
    text(42,373,'右臂第 2 关节目标',20,muted)
    text(42,414,'--' if value is None else '{:.3f} rad'.format(value),44,result_color)
    text(24,514,'设定上限',22,muted);text(254,508,'2.540 rad',32)
    draw.line((24,561,456,561),fill=line,width=1)
    text(24,585,'等待 B35 实板回包' if mask is None else '检查来源：B35 实板回包',22)
    text(24,625,'序号 {}'.format(state.get('sequence','--')),20,muted)
    text(24,663,'会话 {:08X}'.format(state.get('session',0)),20,muted)
    text(24,709,'等待操作' if mask is None else '保留本次诊断结果',20,muted)
    draw.line((24,754,456,754),fill=ink,width=1)
    text(24,773,'只做诊断 · 不输出机械臂动作',16,muted)
    return image


class BoothDisplay:
    def __init__(self, ip, directory, source='recorded_replay'):
        from screen.lcd import Framebuffer
        from screen.cooperative_view import PALETTE
        self.display = Framebuffer(ip,palette=PALETTE,initial_image=render({'source':source}))
        self.directory = Path(directory)
        self.state = {'source':source}; self.lock = threading.Lock();self.generation=0
        self.stop = threading.Event(); self.error = None
        self.worker = threading.Thread(target=self.paint, daemon=True)
        self.worker.start()

    def update(self, **fields):
        with self.lock: self.state.update(fields)

    def accepted(self, request, reply, at, service_us):
        if request.magic != b'FPG1' or request.values[1] == 0xFFFFFFFF:return
        self.update(mask=reply.values[1],sequence=reply.sequence,session=reply.session,
                    value=request.values[6+8]/1e6,build_id=reply.values[8],
                    received_monotonic_ns=at,source='recorded_replay_with_j2_edit')

    def host_claim(self, values, at, session=None):
        pass  # Connectivity probes do not change a diagnostic result.

    def rejected(self, reason, at):
        self.update(last_rejection=str(reason))

    def paint(self):
        try:
            while not self.stop.is_set():
                with self.lock: state = self.state.copy()
                image = render(state)
                state['framebuffer']=self.display.present(image);self.generation+=1
                temporary=self.directory/'booth_lcd.tmp.png';image.save(temporary)
                state.update(display_generation=self.generation,image_sha256=hashlib.sha256(temporary.read_bytes()).hexdigest())
                temporary.replace(self.directory/'booth_lcd.png')
                temporary=self.directory/'booth_lcd.tmp.json';temporary.write_text(json.dumps(state,indent=2)+'\n')
                temporary.replace(self.directory/'booth_lcd.json')
                self.stop.wait(.2)
        except Exception as error:
            self.error = repr(error)

    def close(self):
        self.stop.set(); self.worker.join(timeout=2); self.display.close(preserve=True)
