# Copyright (c) 2021 ARM Limited
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are
# met: redistributions of source code must retain the above copyright
# notice, this list of conditions and the following disclaimer;
# redistributions in binary form must reproduce the above copyright
# notice, this list of conditions and the following disclaimer in the
# documentation and/or other materials provided with the distribution;
# neither the name of the copyright holders nor the names of its
# contributors may be used to endorse or promote products derived from
# this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
# A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
# OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
# SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
# LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
# DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
# THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

"""
Delegato single-die 2×4 mesh NoC configuration for fast testing.

Router layout (row-major numbering, 2 rows × 4 columns):

   col0   col1  col2  col3
   MEM    Core  Core  Misc
   ┌─────────────────────┐
   │  0    1    2    3   │  row 0
   │  4    5    6    7   │  row 1
   └─────────────────────┘

  Core+LLC (CHI_RNF, CHI_HNF): routers 1, 2, 5, 6
  Memory   (CHI_SNF_MainMem):  routers 0, 4
  Misc/Boot:                   router  3
  DMA/IO:                      router  7

No cross-chiplet links (single die).
"""

from ruby import CHI_config


MAIN_MEM_MIN = 0x0
MAIN_MEM_MAX = 0x280000000
BOOT_MEM_MIN = 0x0
BOOT_MEM_MAX = 0x4000000
MN_MIN = 0x0
MN_MAX = 0x400

NODE_ID_BASE = {
    "L1I": 0,
    "L1D": 4,
    "RNF": 8,
    "HNF": 12,
    "AAN": 16,
    "SNF": 20,
    "BOOT_SNF": 22,
    "MN": 23,
    "RNI": 24,
}

OPTIONAL_ROUTE_NODE_IDS = (NODE_ID_BASE["BOOT_SNF"],)
SINGLE_DIE = True

route_table = [
    ("MN", MN_MIN, MN_MAX, 0x0, 0x0, 23),
    ("SNF", BOOT_MEM_MIN, BOOT_MEM_MAX, 0x0, 0x0, 22),

    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, 0xc0, 0x0, 12),
    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, 0xc0, 0x40, 13),
    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, 0xc0, 0x80, 14),
    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, 0xc0, 0xc0, 15),

    ("SNF", MAIN_MEM_MIN, MAIN_MEM_MAX, 0x40, 0x0, 20),
    ("SNF", MAIN_MEM_MIN, MAIN_MEM_MAX, 0x40, 0x40, 21),
]

class NoC_Params(CHI_config.NoC_Params):
    num_rows = 2
    num_cols = 4


_core_routers = [1, 2, 5, 6]
_mem_routers = [0, 4]


class CHI_RNF(CHI_config.CHI_RNF):
    """4 cores distributed on inner routers."""
    class NoC_Params(CHI_config.CHI_RNF.NoC_Params):
        router_list = _core_routers


class CHI_HNF(CHI_config.CHI_HNF):
    """4 LLC/directory slices co-located with cores."""
    class NoC_Params(CHI_config.CHI_HNF.NoC_Params):
        router_list = _core_routers


class CHI_MN(CHI_config.CHI_MN):
    """Misc node on router 3."""
    class NoC_Params(CHI_config.CHI_MN.NoC_Params):
        router_list = [3]


class CHI_SNF_MainMem(CHI_config.CHI_SNF_MainMem):
    """2 memory channels on col 0."""
    class NoC_Params(CHI_config.CHI_SNF_MainMem.NoC_Params):
        router_list = _mem_routers


class CHI_SNF_BootMem(CHI_config.CHI_SNF_BootMem):
    """Boot memory on router 3."""
    class NoC_Params(CHI_config.CHI_SNF_BootMem.NoC_Params):
        router_list = [3]


class CHI_RNI_DMA(CHI_config.CHI_RNI_DMA):
    """DMA on router 7."""
    class NoC_Params(CHI_config.CHI_RNI_DMA.NoC_Params):
        router_list = [7]


class CHI_RNI_IO(CHI_config.CHI_RNI_IO):
    """IO on router 7."""
    class NoC_Params(CHI_config.CHI_RNI_IO.NoC_Params):
        router_list = [7]
