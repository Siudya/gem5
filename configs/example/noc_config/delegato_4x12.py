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
Delegato dual-chiplet 4×12 mesh NoC configuration.

Router layout (row-major numbering, 4 rows × 12 columns):

         Chiplet 0                          Chiplet 1
  col0  col1-4       col5  col6       col7-10      col11
  MEM   Core+LLC     D2D   D2D        Core+LLC     MEM
  ┌──────────────────┐    ┌──────────────────────────┐
  │  0  1  2  3  4  5│←→│ 6  7  8  9 10 11         │  row 0
  │ 12 13 14 15 16 17│←→│18 19 20 21 22 23         │  row 1
  │ 24 25 26 27 28 29│←→│30 31 32 33 34 35         │  row 2
  │ 36 37 38 39 40 41│←→│42 43 44 45 46 47         │  row 3
  └──────────────────┘    └──────────────────────────┘

Cross-chiplet D2D links: (5↔6), (17↔18), (29↔30), (41↔42)
  Latency: 100 cycles @ 2 GHz = 50 ns
"""

from ruby import CHI_config


MAIN_MEM_MIN = 0x200000000
MAIN_MEM_MAX = 0x600000000
BOOT_MEM_MIN = 0x0
BOOT_MEM_MAX = 0x4000000
MN_MIN = 0x0
MN_MAX = 0x400

NODE_ID_BASE = {
    "L1I": 0,
    "L1D": 32,
    "RNF": 64,
    "HNF": 96,
    "AAN": 128,
    "SNF": 136,
    "BOOT_SNF": 144,
    "MN": 145,
    "RNI": 146,
}

OPTIONAL_ROUTE_NODE_IDS = (NODE_ID_BASE["BOOT_SNF"],)

# Address allocation scheme:
#   Die bit: PA[10] -> 1 KiB (16 cacheline) interleave between Die 0 and Die 1
#   HNF: PA[10]+PA[9:6] (5 bits, 32-way interleave across both dies)
#   SNF: PA[10]+PA[9:8] (3 bits, 8-way interleave, PA[9:8]=row => same-row HNF→SNF)
#   AAN: PA[10]+PA[9:8] (same as SNF, same-row proxy to remote die)
#   Cacheline interleaving: PA[5:0] not in masks (64B cacheline)
HNF_MASK = 0x7C0
SNF_MASK = 0x700
AAN_MASK = 0x700

# Helper functions to compute interleaving match values.
# Change _DIE_BIT, _HNF_SHIFT, _SNF_SHIFT to adjust the bit layout.
_DIE_BIT = 10
_HNF_SHIFT = 6           # PA[9:6]
_SNF_SHIFT = 8           # PA[9:8]

def HNF_INTLV(die, slice):
    return (die << _DIE_BIT) | (slice << _HNF_SHIFT)

def SNF_INTLV(die, slice):
    return (die << _DIE_BIT) | (slice << _SNF_SHIFT)

route_table = [
    ("MN", MN_MIN, MN_MAX, 0x0, 0x0, 145),
    ("SNF", BOOT_MEM_MIN, BOOT_MEM_MAX, 0x0, 0x0, 144),

    # HNF: Die 0 (nodes 96-111), PA[33]=0
    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, HNF_MASK, HNF_INTLV(0,  0),  96),
    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, HNF_MASK, HNF_INTLV(0,  1),  97),
    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, HNF_MASK, HNF_INTLV(0,  2),  98),
    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, HNF_MASK, HNF_INTLV(0,  3),  99),
    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, HNF_MASK, HNF_INTLV(0,  4), 100),
    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, HNF_MASK, HNF_INTLV(0,  5), 101),
    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, HNF_MASK, HNF_INTLV(0,  6), 102),
    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, HNF_MASK, HNF_INTLV(0,  7), 103),
    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, HNF_MASK, HNF_INTLV(0,  8), 104),
    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, HNF_MASK, HNF_INTLV(0,  9), 105),
    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, HNF_MASK, HNF_INTLV(0, 10), 106),
    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, HNF_MASK, HNF_INTLV(0, 11), 107),
    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, HNF_MASK, HNF_INTLV(0, 12), 108),
    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, HNF_MASK, HNF_INTLV(0, 13), 109),
    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, HNF_MASK, HNF_INTLV(0, 14), 110),
    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, HNF_MASK, HNF_INTLV(0, 15), 111),
    # HNF: Die 1 (nodes 112-127), PA[33]=1
    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, HNF_MASK, HNF_INTLV(1,  0), 112),
    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, HNF_MASK, HNF_INTLV(1,  1), 113),
    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, HNF_MASK, HNF_INTLV(1,  2), 114),
    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, HNF_MASK, HNF_INTLV(1,  3), 115),
    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, HNF_MASK, HNF_INTLV(1,  4), 116),
    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, HNF_MASK, HNF_INTLV(1,  5), 117),
    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, HNF_MASK, HNF_INTLV(1,  6), 118),
    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, HNF_MASK, HNF_INTLV(1,  7), 119),
    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, HNF_MASK, HNF_INTLV(1,  8), 120),
    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, HNF_MASK, HNF_INTLV(1,  9), 121),
    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, HNF_MASK, HNF_INTLV(1, 10), 122),
    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, HNF_MASK, HNF_INTLV(1, 11), 123),
    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, HNF_MASK, HNF_INTLV(1, 12), 124),
    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, HNF_MASK, HNF_INTLV(1, 13), 125),
    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, HNF_MASK, HNF_INTLV(1, 14), 126),
    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, HNF_MASK, HNF_INTLV(1, 15), 127),

    # SNF: Die 0 (nodes 136-139), PA[33]=0
    ("SNF", MAIN_MEM_MIN, MAIN_MEM_MAX, SNF_MASK, SNF_INTLV(0, 0), 136),
    ("SNF", MAIN_MEM_MIN, MAIN_MEM_MAX, SNF_MASK, SNF_INTLV(0, 1), 137),
    ("SNF", MAIN_MEM_MIN, MAIN_MEM_MAX, SNF_MASK, SNF_INTLV(0, 2), 138),
    ("SNF", MAIN_MEM_MIN, MAIN_MEM_MAX, SNF_MASK, SNF_INTLV(0, 3), 139),
    # SNF: Die 1 (nodes 140-143), PA[33]=1
    ("SNF", MAIN_MEM_MIN, MAIN_MEM_MAX, SNF_MASK, SNF_INTLV(1, 0), 140),
    ("SNF", MAIN_MEM_MIN, MAIN_MEM_MAX, SNF_MASK, SNF_INTLV(1, 1), 141),
    ("SNF", MAIN_MEM_MIN, MAIN_MEM_MAX, SNF_MASK, SNF_INTLV(1, 2), 142),
    ("SNF", MAIN_MEM_MIN, MAIN_MEM_MAX, SNF_MASK, SNF_INTLV(1, 3), 143),

    # AAN: Die 0 (nodes 128-131), proxy remote addr (Die 1 PA[33]=1)
    ("AAN", MAIN_MEM_MIN, MAIN_MEM_MAX, AAN_MASK, SNF_INTLV(1, 0), 128),
    ("AAN", MAIN_MEM_MIN, MAIN_MEM_MAX, AAN_MASK, SNF_INTLV(1, 1), 129),
    ("AAN", MAIN_MEM_MIN, MAIN_MEM_MAX, AAN_MASK, SNF_INTLV(1, 2), 130),
    ("AAN", MAIN_MEM_MIN, MAIN_MEM_MAX, AAN_MASK, SNF_INTLV(1, 3), 131),
    # AAN: Die 1 (nodes 132-135), proxy remote addr (Die 0 PA[33]=0)
    ("AAN", MAIN_MEM_MIN, MAIN_MEM_MAX, AAN_MASK, SNF_INTLV(0, 0), 132),
    ("AAN", MAIN_MEM_MIN, MAIN_MEM_MAX, AAN_MASK, SNF_INTLV(0, 1), 133),
    ("AAN", MAIN_MEM_MIN, MAIN_MEM_MAX, AAN_MASK, SNF_INTLV(0, 2), 134),
    ("AAN", MAIN_MEM_MIN, MAIN_MEM_MAX, AAN_MASK, SNF_INTLV(0, 3), 135),
]

class NoC_Params(CHI_config.NoC_Params):
    num_rows = 4
    num_cols = 12
    # D2D cross-chiplet links between col 5 and col 6
    cross_links = [
        (5, 6), (6, 5),
        (17, 18), (18, 17),
        (29, 30), (30, 29),
        (41, 42), (42, 41),
    ]
    cross_link_latency = 100


# Chiplet 0 core+LLC columns: col 1-4
# Chiplet 1 core+LLC columns: col 7-10
_core_routers = [
    # Chiplet 0: col 1-4, rows 0-3
    1, 2, 3, 4,
    13, 14, 15, 16,
    25, 26, 27, 28,
    37, 38, 39, 40,
    # Chiplet 1: col 7-10, rows 0-3
    7, 8, 9, 10,
    19, 20, 21, 22,
    31, 32, 33, 34,
    43, 44, 45, 46,
]

# Memory columns: col 0 (Chiplet 0) and col 11 (Chiplet 1)
_mem_routers = [
    0, 12, 24, 36,   # Chiplet 0
    11, 23, 35, 47,  # Chiplet 1
]

_aan_routers = [
    5, 17, 29, 41,   # Chiplet 0 requester-side D2D boundary
    6, 18, 30, 42,   # Chiplet 1 requester-side D2D boundary
]


class CHI_RNF(CHI_config.CHI_RNF):
    """Cores distributed across col 1-4 and col 7-10 routers (round-robin)."""
    class NoC_Params(CHI_config.CHI_RNF.NoC_Params):
        router_list = _core_routers


class CHI_HNF(CHI_config.CHI_HNF):
    """LLC/directory slices distributed across col 1-4 and col 7-10 routers."""
    class NoC_Params(CHI_config.CHI_HNF.NoC_Params):
        router_list = _core_routers


class CHI_AAN(CHI_config.CHI_AAN):
    """AMO aggregation nodes at requester-side D2D boundary routers."""
    class NoC_Params(CHI_config.CHI_AAN.NoC_Params):
        router_list = _aan_routers


class CHI_MN(CHI_config.CHI_MN):
    """Misc node on Chiplet 0, col 0, row 0."""
    class NoC_Params(CHI_config.CHI_MN.NoC_Params):
        router_list = [0]


class CHI_SNF_MainMem(CHI_config.CHI_SNF_MainMem):
    """8 DDR5 channels: 4 on col 0 (Chiplet 0), 4 on col 11 (Chiplet 1)."""
    class NoC_Params(CHI_config.CHI_SNF_MainMem.NoC_Params):
        router_list = _mem_routers


class CHI_SNF_BootMem(CHI_config.CHI_SNF_BootMem):
    """Boot memory on Chiplet 0, col 0, row 0."""
    class NoC_Params(CHI_config.CHI_SNF_BootMem.NoC_Params):
        router_list = [0]


class CHI_RNI_DMA(CHI_config.CHI_RNI_DMA):
    """DMA on Chiplet 0, col 0, row 0."""
    class NoC_Params(CHI_config.CHI_RNI_DMA.NoC_Params):
        router_list = [0]


class CHI_RNI_IO(CHI_config.CHI_RNI_IO):
    """IO on Chiplet 0, col 0, row 0."""
    class NoC_Params(CHI_config.CHI_RNI_IO.NoC_Params):
        router_list = [0]
