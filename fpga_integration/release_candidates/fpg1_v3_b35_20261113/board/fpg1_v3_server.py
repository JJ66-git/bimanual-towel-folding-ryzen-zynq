"""Single-owner B35 UDP observer endpoint. No CAN, robot actions or watchdog commits."""
import argparse
import ipaddress
import json
from pathlib import Path
import signal
import socket
import time
import os
import queue
import threading
from board.fold_bridge import FoldPynqGuard,FoldSession
from board.ownership import exclusive_owner
from board.watchdog_monitor import Journal
from host.reference import load_profile
from host import fold_protocol as wire
from host import task_status
from host import action_status

DEFAULT_BITFILE=(Path(__file__).resolve().parents[1]/'overlay/lcd_framebuffer_fpg1_v3_b35_20261113_revision/fpg1_v3_b35_20261113_revision.bit')


class BufferedJournal(Journal):
    """Bounded ordered diagnostics; filesystem writes never run in the UDP owner.

    Overflow or disk failure stops service instead of silently losing evidence.
    Shutdown drains the writer; no grant, deadline or robot behavior changes.
    """
    def __init__(self,path,*,capacity=4096,output=None):
        if output is None:super().__init__(path)
        else:self.output=output
        self.pending=queue.Queue(maxsize=capacity)
        self.error=None;self.closed=False;self.written=0;self.max_write_us=0
        self.worker=threading.Thread(target=self._write_rows,name='board-journal',daemon=True)
        self.worker.start()

    def write(self,event,**values):
        if self.error or self.closed:raise RuntimeError('Board journal unavailable: '+str(self.error or 'closed'))
        row=json.dumps(dict(event=event,board_monotonic_ns=time.monotonic_ns(),**values),separators=(',',':'))+'\n'
        try:self.pending.put_nowait(row)
        except queue.Full:
            self.error='bounded journal backlog exhausted'
            raise RuntimeError(self.error)

    def _write_rows(self):
        try:
            while True:
                row=self.pending.get()
                if row is None:break
                started=time.monotonic_ns()
                self.output.write(row);self.output.flush()
                self.max_write_us=max(self.max_write_us,(time.monotonic_ns()-started)/1000)
                self.written+=1
            self.output.write(json.dumps(dict(event='journal_closed',written_rows=self.written,
                max_file_write_us=self.max_write_us,error=self.error))+'\n')
            self.output.flush()
        except Exception as error:self.error=type(error).__name__+': '+str(error)
        finally:
            try:self.output.close()
            except Exception as error:self.error=self.error or str(error)

    @property
    def running(self):return self.error is None and not self.closed

    def close(self):
        if self.closed:return
        self.closed=True
        if self.worker.is_alive():
            try:self.pending.put(None,timeout=5)
            except queue.Full:raise TimeoutError('Board journal shutdown backlog')
            self.worker.join(5)
        if self.worker.is_alive():raise TimeoutError('Board journal did not finish')
        if self.error:raise RuntimeError('Board journal failed: '+self.error)


def matched_task_claim(identity,values,task_owner):
    matched=task_owner is not None and identity==task_owner[1]
    terminal=values['phase'] in ('ENDED','FAILED','STOPPED','COMPLETE') or values['activity']=='ERROR'
    if terminal and not matched:raise ValueError('task_status_session_mismatch')
    return matched


def serve(session,journal,bind,port,peer,display=None,source='synthetic_test'):
    fui_owner=None;fui_sequence=-1;fui_tick=-1;fui_last=0.;samples=0
    authority_receiver=action_status.Receiver()
    reset_receiver=task_status.ResetClaims();ended_session=None
    health_path=journal.output.name if hasattr(journal,'output') else None
    health_path=Path(health_path).parent/'service_health.json' if health_path else None
    health=dict(pid=os.getpid(),source=source,udp_bound=False,display_ready=False,renderer_pid=None)
    def save_health(**fields):
        health.update(fields)
        if health_path:
            temporary=health_path.with_suffix('.tmp');temporary.write_text(json.dumps(health)+'\n');temporary.replace(health_path)
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
        sock.bind((bind,port));sock.settimeout(.1)
        save_health(udp_bound=True)
        journal.write('fold_server_started',bind=bind,port=port,peer=peer,build_id=wire.BUILD_ID,source=source,phase_config=session.guard.fold_identity,calibration_status=session.guard.fold_profile['calibration_status'])
        while journal.running:
            packet=None
            try:packet,address=sock.recvfrom(4096)
            except socket.timeout:pass
            if packet is not None:
                received=time.monotonic_ns()
                try:
                    if address[0]!=peer:raise ValueError('source_ip')
                    if packet[:4]==b'FAS1':
                        last=getattr(session,'last_result',None)
                        response,identity,values=authority_receiver.accept(packet,address,getattr(session,'owner',None),
                            session.last_sequence,source,last[1] if last is not None else None)
                        journal.write('authority_display_claim',values=values,session=identity,source=source,motion_permission=False)
                    elif packet[:4]==b'FUI1':
                        kind,identity,sequence,tick,values=task_status.unpack(packet)
                        now=time.monotonic()
                        owner=(address,identity)
                        if kind!=1:raise ValueError('expected_fui_request')
                        resetting=values['phase'] in task_status.RESET_PHASES
                        if resetting:
                            task_owner=getattr(session,'owner',None)
                            active=(task_owner is not None and task_owner[1]!=ended_session
                                    and getattr(session,'last_step',None) is not None and now-session.last_success<5)
                            reset_receiver.accept(identity,sequence,tick,values,address,now,task_active=active)
                        else:
                            matched_task=matched_task_claim(identity,values,getattr(session,'owner',None))
                            if fui_owner is not None and owner!=fui_owner and now-fui_last<5 and not matched_task:raise ValueError('fui_lease_active')
                            if owner==fui_owner and (sequence<=fui_sequence or tick<=fui_tick):raise ValueError('old_fui_status')
                            if matched_task and values['phase'] in ('ENDED','FAILED','STOPPED','COMPLETE'):ended_session=identity
                        response=task_status.pack(129,identity,sequence,tick,values)
                        if not resetting:fui_owner,fui_sequence,fui_tick,fui_last=owner,sequence,tick,now
                        journal.write('host_task_claim',values=values,session=identity,sequence=sequence,tick_us=tick,source=source,overrides_pl=False)
                    else:
                        if packet[:4]==b'FVE1' and source=='host_observation':
                            incoming=wire.decode(packet)
                            if incoming.kind==1 and incoming.values[-1]:
                                raise ValueError('field_calibration_pending')
                        response=session.handle(packet,address)
                        frame=wire.decode(response)
                        if frame.magic==b'FPG1' and wire.decode(packet).values[1]!=wire.UNKNOWN:
                            samples+=1
                            ended_session=None

                    sent=sock.sendto(response,address)
                    if display is not None:
                        if packet[:4]==b'FAS1':display.authority_claim(values,received,identity)
                        elif packet[:4]==b'FUI1':display.host_claim(values,received,identity)
                        else:display.accepted(wire.decode(packet),frame,received,(time.monotonic_ns()-received)/1000)
                    journal.write('fold_reply_sent',peer=list(address),request_bytes=len(packet),response_bytes=sent,
                                  board_received_ns=received,source=source,request_hex=packet.hex(),response_hex=response.hex())
                except ValueError as error:
                    if display is not None:display.rejected(str(error),received)
                    journal.write('fold_packet_rejected',peer=list(address),bytes=len(packet),board_received_ns=received,
                                  error=str(error),header_hex=packet[:24].hex(),source=source)
            if display is not None:
                # Same owner thread performs guard reads between transactions.
                state=session.guard.read(0xE0)
                display.update(phase=session.guard.read(0x90)&7,verify=state&3,reason=(state>>4)&15,
                    stable=(state>>16)&255,event=session.guard.read(0xE4),cycles=session.guard.read(0x9C),watchdog_control=session.guard.read(0x4C),frame_id=session.guard.read(0xE8),event_session=session.guard.read(0xEC))
                if display.error:
                    journal.write('fold_display_failed',error=display.error,protocol_service_continues=True)
                    save_health(display_ready=False,display_error=display.error)
                    if hasattr(display,'ready'):display.close(wait=False)
                    else:display.close()
                    display=None
                elif not health['display_ready'] and getattr(display,'ready',True):
                    save_health(display_ready=True,renderer_pid=getattr(display,'child_pid',None))
                    journal.write('fold_display_ready',renderer_pid=getattr(display,'child_pid',None))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bitfile',type=Path,default=DEFAULT_BITFILE)
    parser.add_argument('--bind',default='0.0.0.0');parser.add_argument('--port',type=int,default=9009)
    parser.add_argument('--peer',required=True);parser.add_argument('--log',type=Path,required=True)
    parser.add_argument('--source',choices=['synthetic_test','recorded_replay','host_observation'],default='synthetic_test')
    parser.add_argument('--lcd',action='store_true')
    parser.add_argument('--booth',action='store_true',help='show diagnostic STOP/PASS on the LCD')
    parser.add_argument('--isolated-lcd',action='store_true',help='render LCD in its own process')
    parser.add_argument('--bulk-vectors',action='store_true',help='write six signed vectors through the MMIO array')
    args=parser.parse_args();ipaddress.IPv4Address(args.peer)
    if args.booth and args.source!='recorded_replay':parser.error('booth display requires recorded_replay source')
    if not 1<=args.port<=65535 or not args.bitfile.is_file():parser.error('invalid port/bitstream')
    if args.log.exists() or not args.log.parent.is_dir():parser.error('log must be new in an existing directory')
    def stop(signum,frame):raise KeyboardInterrupt
    signal.signal(signal.SIGINT,stop);signal.signal(signal.SIGTERM,stop)
    with exclusive_owner():
        from pynq import Overlay,Clocks
        from board.clock_reference import configure_reference,verify_fclk0
        configure_reference()
        overlay=Overlay(str(args.bitfile))
        verify_fclk0()
        profile=load_profile();guard=FoldPynqGuard(overlay.guard,profile,bulk_vectors=args.bulk_vectors)
        print('FOLD_CONFIG_IDENTITY='+json.dumps(guard.fold_identity),flush=True)
        journal=BufferedJournal(args.log);display=None
        try:
            if args.lcd:
                if args.isolated_lcd:
                    from board.isolated_display import IsolatedDisplay
                    display=IsolatedDisplay(overlay.lcd,args.log.parent,args.source,booth=args.booth)
                elif args.booth:
                    from board.booth_display import BoothDisplay as FoldDisplay
                else:
                    from board.fold_display import FoldDisplay
                if not args.isolated_lcd:display=FoldDisplay(overlay.lcd,args.log.parent,args.source)
                display.update(phase_config_crc=guard.fold_identity['crc32'],calibration_status=guard.fold_profile['calibration_status'],config_matched=True,max_actions=profile['max_actions'],min_frames=guard.fold_profile['vision']['min_frames'],feedback_limit_us=guard.fold_profile['timing']['feedback_age_us'])
            serve(FoldSession(args.peer,guard,profile),journal,args.bind,args.port,args.peer,display,args.source)
        except KeyboardInterrupt:pass
        finally:
            if display is not None:display.close()
            journal.close()

if __name__=='__main__':main()
