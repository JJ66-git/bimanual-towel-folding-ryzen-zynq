"""Real PL self-test with explicitly SYNTHETIC samples; no camera, CAN or robot access."""
import json
from pathlib import Path
import secrets
import time


def run(ip,out):
    from host import fold_protocol as wire
    from host.protocol import Sample,UNKNOWN
    from host.reference import load_profile,profile_crc,evaluate
    from board.fold_bridge import FoldPynqGuard,FoldSession
    root=Path(__file__).resolve().parents[1]
    profile=load_profile(root/'config/guard_profile.json')
    crc=profile_crc(profile);guard=FoldPynqGuard(ip,profile)
    now=[0.];service=FoldSession('192.168.2.10',guard,profile,clock=lambda:now[0])
    session=secrets.randbelow(0xFFFFFFFE)+1
    sequence=[0];step=[0];elapsed=[0];rows=[]
    zeros=(0,)*14;start=tuple(profile['start']);address=('192.168.2.10',32001)
    def exchange(request):
        begin=time.monotonic_ns();reply=service.handle(request,address)
        frame=wire.decode(reply)
        rows.append({'source':'SYNTHETIC','request_hex':request.hex(),'reply_hex':reply.hex(),
                     'transaction_us':(time.monotonic_ns()-begin)//1000,'values':list(frame.values)})
        return frame
    def probe(identity):
        sample=Sample(crc,UNKNOWN,0,0,UNKNOWN,UNKNOWN,start,start,start,start,zeros,zeros)
        request=wire.encode(b'FPG1',3,1,identity,0,time.monotonic_ns()//1000,wire.FPG1_REQUEST.unpack(sample.pack()))
        reply=exchange(request)
        assert reply.values[8]==wire.BUILD_ID and reply.values[1]==0,list(reply.values)
        return reply
    def send(pose):
        sequence[0]+=1
        sample=Sample(crc,step[0],4,elapsed[0],12000,0,pose,pose,pose,pose,zeros,zeros)
        request=wire.encode(b'FPG1',3,1,session,sequence[0],time.monotonic_ns()//1000,wire.FPG1_REQUEST.unpack(sample.pack()))
        reply=exchange(request);expected=evaluate(sample,profile)
        assert reply.values[1:4]==(expected['violations'],expected['events'],expected['phase']),list(reply.values)
        assert reply.values[1]==0,list(reply.values)
        step[0]+=1;elapsed[0]+=50000
        return reply
    initial=probe(session)
    opened=list(start);opened[6]=opened[13]=40000
    for _ in range(3):reply=send(tuple(opened))
    assert reply.values[5]==1,list(reply.values)
    gripped=opened[:];gripped[6]=gripped[13]=6000
    for _ in range(3):reply=send(tuple(gripped))
    assert reply.values[5]==2,list(reply.values)
    folded=gripped[:];folded[1]=folded[8]=1730000;folded[2]=folded[9]=-896000
    for _ in range(4):reply=send(tuple(folded))
    assert reply.values[5]==3,list(reply.values)
    retreat=folded[:];retreat[1]=retreat[8]=500000;retreat[2]=retreat[9]=-300000
    for _ in range(4):reply=send(tuple(retreat))
    event=reply.values[9]
    assert event!=UNKNOWN and ((reply.values[6]>>8)&3)==1,list(reply.values)
    event_rx=time.monotonic_ns()//1000
    feature_results=[]
    for i in range(3):
        while time.monotonic_ns()//1000 < event_rx+i*100000+1000:time.sleep(.001)
        send(tuple(retreat))  # Real new SYNTHETIC joint sample; never pretends to be SDK feedback.
        ready=time.monotonic_ns()//1000
        packet=wire.encode(b'FVE1',1,1,session,event,ready,(event_rx,i+1,4290,320,240,9000,1500,1))
        result=exchange(packet);feature_results.append(list(result.values))
        assert result.values[3]==i+1,list(result.values)
    assert feature_results[-1][1]==2,feature_results
    query=wire.encode(b'FVE1',1,3,session,event,time.monotonic_ns()//1000,())
    result=exchange(query)
    assert result.values==(3,2,0,3),list(result.values)
    new_session=session+1 if session<0xFFFFFFFE else 1
    now[0]=6.;sequence[0]=0;step[0]=0;elapsed[0]=0
    session=new_session
    probe(session);send(start)
    # A second synthetic round checks the PL's independent 1.5 second deadline.
    for pose,count in [(opened,3),(gripped,3),(folded,4),(retreat,4)]:
        for _ in range(count):reply=send(tuple(pose))
    timeout_event=reply.values[9]
    assert timeout_event!=UNKNOWN
    deadline_start=time.monotonic()
    while time.monotonic()-deadline_start<2.1:
        current=wire.FVE1_RESULT.unpack(guard.query(session,timeout_event))
        if current[1]==3:break
        time.sleep(.01)
    deadline_elapsed=time.monotonic()-deadline_start
    assert current[1:3]==(3,8),current
    assert 1.3<=deadline_elapsed<=1.8,deadline_elapsed
    assert ip.read(0x4C)==0,'FPG1/FVE1 must not arm the watchdog' 
    record={'passed':True,'source':'SYNTHETIC','real_pl':True,'build_id':hex(ip.read(0)),
            'checks':['FPG1 probe','OPEN','GRIP','FOLD','retreat event','three fresh stable FVE1 frames',
                      'terminal query','new-session step reset','autonomous 1.5s timeout','watchdog remains off'],
            'autonomous_timeout_s':deadline_elapsed,'feature_results':feature_results,'packets':rows,'robot_commands_sent':False,
            'normative_release_ready':False,
            'known_rtl_limits':[], 'calibration_pending':True,
            'qualification':'Synthetic PL subset test, not full conformance, camera calibration or task success'}
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    (out/('pl_synthetic_'+str(time.time_ns())+'.json')).write_text(json.dumps(record,indent=2)+'\n')
    return record


def check_lcd(ip,out):
    from screen.lcd import Framebuffer
    from PIL import Image,ImageDraw
    display=Framebuffer(ip)
    image=Image.new('RGB',(480,800),(5,17,29));draw=ImageDraw.Draw(image)
    try:
        from PIL import ImageFont
        font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',24)
    except OSError:font=None
    for i,line in enumerate(['FlexiFold / B35','Zynq-7020 interface check','FPG1 v3 / FVE1 v1',
                             'SYNTHETIC SELF TEST','No robot commands','LCD framebuffer active']):
        draw.text((28,45+i*70),line,fill=(236,245,255),font=font)
    before=ip.read(0x18)
    try:result=display.present(image);time.sleep(.1);result['scan_frames_delta']=int(ip.read(0x18))-before
    finally:display.close()
    result['physical_display_verified']=False
    out=Path(out);out.mkdir(parents=True,exist_ok=True);image.save(out/'lcd_self_test.png')
    (out/'lcd_interface.json').write_text(json.dumps(result,indent=2)+'\n')
    return result
