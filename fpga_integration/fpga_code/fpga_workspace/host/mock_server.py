"""Optional UDP software endpoint for link bring-up, without a robot or FPGA."""
import argparse
import socket
from .reference import emulate, load_profile


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bind',default='127.0.0.1')
    parser.add_argument('--port',type=int,default=9009)
    parser.add_argument('--count',type=int,default=1)
    args=parser.parse_args()
    if args.count < 1:
        parser.error('count must be positive')
    profile=load_profile()
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
        sock.bind((args.bind,args.port))
        for _ in range(args.count):
            packet,address=sock.recvfrom(4096)
            try:
                reply=emulate(packet,profile)
            except (ValueError,OverflowError):
                continue
            sock.sendto(reply,address)


if __name__=='__main__':
    main()
