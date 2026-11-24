"""Zynq PS Linux UDP-to-AXI bridge for the supplied PL reference checker.

Requires an already loaded bitstream, an exclusive UIO map, and board Linux.
This is not a software FPGA emulator and never opens CAN or robot interfaces.
"""
import argparse
import mmap
import math
import os
import socket
import struct
import time

from host.protocol import RESPONSE, Sample, frame, unframe
from host.reference import load_profile, profile_crc

VECTOR_BASES=(0x100,0x140,0x180,0x1c0,0x200,0x240)


class GuardUIO:
    def __init__(self,path,timeout=0.1):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('timeout must be finite and positive')
        import fcntl
        self.timeout=timeout
        self.fd=os.open(path,os.O_RDWR|os.O_SYNC)
        self.memory=None
        try:
            fcntl.flock(self.fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.memory=mmap.mmap(self.fd,4096,flags=mmap.MAP_SHARED,
                prot=mmap.PROT_READ|mmap.PROT_WRITE,offset=0)
            if self.read(0)!=0x46504731:
                raise RuntimeError('Wrong UIO region or PL design ID')
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.memory is not None:
            self.memory.close(); self.memory=None
        if self.fd is not None:
            os.close(self.fd); self.fd=None

    def read(self,address):
        return struct.unpack_from('<I',self.memory,address)[0]

    def write(self,address,value):
        struct.pack_into('<I',self.memory,address,value&0xffffffff)

    def check(self,sample):
        if self.read(8)&1:
            raise RuntimeError('PL busy before request')
        for offset,value in ((0x10,sample.profile_crc),(0x14,sample.step),(0x18,sample.flags),
                             (0x1c,sample.feedback_age_us),(0x20,sample.queue_depth)):
            self.write(offset,value)
        for base,vector in zip(VECTOR_BASES,(sample.target,sample.measured,sample.previous,
                                            sample.act,sample.correction,sample.previous_correction)):
            for index,value in enumerate(vector):
                self.write(base+index*4,value)
        self.write(4,1)
        deadline=time.monotonic()+self.timeout
        while True:
            status=self.read(8)
            if not status&1 and status&2:
                return self.read(0x40),self.read(0x44),self.read(0x48)
            if time.monotonic()>=deadline:
                raise TimeoutError('PL result timeout; no motion decision produced')
            time.sleep(0.0001)


def serve_packet(packet,device,profile):
    kind,session,sequence,tick,payload=unframe(packet)
    if kind!=1:
        raise ValueError('Expected request')
    sample=Sample.unpack(payload)
    if sample.profile_crc!=profile_crc(profile):
        raise ValueError('Host/PS profile mismatch')
    started=time.monotonic_ns()
    violations,events,phase=device.check(sample)
    elapsed=min(0xffffffff,(time.monotonic_ns()-started)//1000)
    return frame(2,session,sequence,tick,RESPONSE.pack(sample.profile_crc,violations,events,phase,elapsed))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--uio',required=True)
    parser.add_argument('--bind',default='0.0.0.0')
    parser.add_argument('--port',type=int,default=9009)
    parser.add_argument('--pl-timeout',type=float,default=0.1)
    args=parser.parse_args()
    if (not math.isfinite(args.pl_timeout) or args.pl_timeout<=0
            or not 1<=args.port<=65535):
        parser.error('pl-timeout and port must be finite and valid')
    profile=load_profile()
    device=GuardUIO(args.uio,args.pl_timeout)
    active=None; last_sequence=-1; last_tick=-1
    try:
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
            sock.bind((args.bind,args.port))
            while True:
                packet,address=sock.recvfrom(4096)
                try:
                    kind,session,sequence,tick,payload=unframe(packet)
                    identity=(address,session)
                    if active is not None and identity!=active:
                        raise ValueError('Different session; restart bridge explicitly to re-arm')
                    if kind!=1 or sequence<=last_sequence or tick<=last_tick:
                        raise ValueError('Old, duplicate or invalid request')
                    if active is None and sequence!=0:
                        raise ValueError('First sequence must be zero')
                    reply=serve_packet(packet,device,profile)
                    active=identity; last_sequence=sequence; last_tick=tick
                    sock.sendto(reply,address)
                except ValueError as error:
                    print('DROP',error,flush=True)
                # PL timeout/device errors exit the server, rather than claiming a verdict.
    finally:
        device.close()


if __name__=='__main__':
    main()
