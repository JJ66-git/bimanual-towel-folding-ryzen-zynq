"""Exercise the real UDP transport against a localhost software endpoint."""
import json
from pathlib import Path
import socket
import threading

from .link import udp_exchange, response_matches
from .protocol import RUNNING, Sample, frame
from .reference import emulate, load_profile, profile_crc


def check():
    profile=load_profile()
    pose=tuple(profile['start'])
    sample=Sample(profile_crc(profile),0,RUNNING,0,0xffffffff,0xffffffff,
                  pose,pose,pose,pose,(0,)*14,(0,)*14)
    request=frame(1,42,0,1,sample.pack())
    errors=[]
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as server:
        server.bind(('127.0.0.1',0))
        server.settimeout(2)
        def respond():
            try:
                packet,address=server.recvfrom(4096)
                server.sendto(emulate(packet,profile),address)
            except Exception as error:
                errors.append(repr(error))
        thread=threading.Thread(target=respond,daemon=True)
        thread.start()
        try:
            reply,_=list(udp_exchange(server.getsockname(),[request],1))[0]
        finally:
            thread.join(3)
        if errors or thread.is_alive() or not response_matches(reply,request):
            raise RuntimeError('UDP loopback failed: '+str(errors))
    return dict(passed=True,transport='real localhost UDP sockets',fpga_connected=False,
                robot_access=False)


if __name__=='__main__':
    print(json.dumps(check(),indent=2))
