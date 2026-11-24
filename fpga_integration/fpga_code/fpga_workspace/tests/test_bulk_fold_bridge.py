"""Both AXI write paths must commit exactly the same signed 84-word vectors."""
import struct
from types import SimpleNamespace
import unittest
import numpy as np
from board.fold_bridge import FoldPynqGuard,VECTOR_BASES
from host.fold_config import load,identity
from host.reference import load_profile,profile_crc
from host.protocol import Sample,RUNNING
from host import fold_protocol as wire


class BulkTests(unittest.TestCase):
    def test_bulk_and_scalar_paths_preserve_gaps_sign_bits_and_commit_order(self):
        profile=load_profile();vectors=tuple(tuple((-1 if i%2 else 1)*(v*100+i) for i in range(14)) for v in range(1,7))
        sample=Sample(profile_crc(profile),1,RUNNING,1000,12000,0,*vectors)
        results=[];write_counts=[]
        for bulk in (False,True):
            class IP:
                def __init__(self):
                    self.mmio=SimpleNamespace(array=np.full(0x400//4,0xa5a5a5a5,dtype=np.uint32));self.writes=[]
                def read(self,offset):return {0:wire.BUILD_ID,8:2,0x98:identity(load())['crc32'],0x9c:15}.get(offset,0)
                def write(self,offset,value):
                    self.writes.append((offset,value));self.mmio.array[offset//4]=value
                    if offset==4:
                        for base,vector in zip(VECTOR_BASES,vectors):
                            expected=np.asarray(vector,dtype='<i4').view('<u4')
                            np.testing.assert_array_equal(self.mmio.array[base//4:base//4+14],expected)
            ip=IP();guard=FoldPynqGuard(ip,profile,bulk_vectors=bulk);results.append(guard.check(sample,123,1));write_counts.append(len(ip.writes))
            for base in VECTOR_BASES:self.assertEqual(ip.mmio.array[base//4+14:base//4+16].tolist(),[0xa5a5a5a5]*2)
        self.assertEqual(write_counts[0]-write_counts[1],84)
        # Board-side elapsed time is measured separately; the functional fields agree.
        a=list(wire.FPG1_REPLY.unpack(results[0]));b=list(wire.FPG1_REPLY.unpack(results[1]));a[4]=b[4]=0
        self.assertEqual(a,b)
    def test_ip_without_a_numpy_mmio_window_retains_scalar_access(self):
        class IP:
            def read(self,offset):return {0:wire.BUILD_ID,0x98:identity(load())['crc32']}.get(offset,0)
            def write(self,*args):pass
        self.assertFalse(FoldPynqGuard(IP(),load_profile()).bulk_vectors)


if __name__=='__main__':unittest.main()
