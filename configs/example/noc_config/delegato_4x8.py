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
Delegato dual-chiplet 4×8 mesh NoC configuration.

Router layout (row-major numbering, 4 rows × 8 columns):

         Chiplet 0              Chiplet 1
  col0  col1-2   col3  col4   col5-6   col7
  MEM   Core+LLC D2D   D2D    Core+LLC MEM
  ┌──────────────┐    ┌──────────────────┐
  │  0  1  2  3 │←→│ 4  5  6  7         │  row 0
  │  8  9 10 11 │←→│12 13 14 15         │  row 1
  │ 16 17 18 19 │←→│20 21 22 23         │  row 2
  │ 24 25 26 27 │←→│28 29 30 31         │  row 3
  └──────────────┘    └──────────────────┘

Cross-chiplet D2D links: (3↔4), (11↔12), (19↔20), (27↔28)
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
    "L1D": 16,
    "RNF": 32,
    "HNF": 48,
    "AAN": 64,
    "SNF": 72,
    "BOOT_SNF": 80,
    "MN": 81,
    "RNI": 82,
}

OPTIONAL_ROUTE_NODE_IDS = (NODE_ID_BASE["BOOT_SNF"],)

# Address allocation scheme:
#   Die bit: PA[9] -> 512 B (8 cacheline) interleave between Die 0 and Die 1
#   HNF: PA[9]+PA[8:6] (4 bits, 16-way interleave across both dies)
#   SNF: PA[9]+PA[8:7] (3 bits, 8-way interleave, PA[8:7]=row => same-row HNF→SNF)
#   AAN: PA[9]+PA[8:7] (same as SNF, same-row proxy to remote die)
#   Cacheline interleaving: PA[5:0] not in masks (64B cacheline)
HNF_MASK = 0x3C0
SNF_MASK = 0x380
AAN_MASK = 0x380

route_table = [
    ("MN", MN_MIN, MN_MAX, 0x0, 0x0, 81),
    ("SNF", BOOT_MEM_MIN, BOOT_MEM_MAX, 0x0, 0x0, 80),

    # HNF: Die 0 (nodes 48-55), PA[9]=0
    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, HNF_MASK, 0x000, 48),
    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, HNF_MASK, 0x040, 49),
    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, HNF_MASK, 0x080, 50),
    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, HNF_MASK, 0x0C0, 51),
    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, HNF_MASK, 0x100, 52),
    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, HNF_MASK, 0x140, 53),
    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, HNF_MASK, 0x180, 54),
    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, HNF_MASK, 0x1C0, 55),
    # HNF: Die 1 (nodes 56-63), PA[9]=1
    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, HNF_MASK, 0x200, 56),
    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, HNF_MASK, 0x240, 57),
    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, HNF_MASK, 0x280, 58),
    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, HNF_MASK, 0x2C0, 59),
    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, HNF_MASK, 0x300, 60),
    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, HNF_MASK, 0x340, 61),
    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, HNF_MASK, 0x380, 62),
    ("HNF", MAIN_MEM_MIN, MAIN_MEM_MAX, HNF_MASK, 0x3C0, 63),

    # SNF: Die 0 (nodes 72-75), PA[9]=0
    ("SNF", MAIN_MEM_MIN, MAIN_MEM_MAX, SNF_MASK, 0x000, 72),
    ("SNF", MAIN_MEM_MIN, MAIN_MEM_MAX, SNF_MASK, 0x080, 73),
    ("SNF", MAIN_MEM_MIN, MAIN_MEM_MAX, SNF_MASK, 0x100, 74),
    ("SNF", MAIN_MEM_MIN, MAIN_MEM_MAX, SNF_MASK, 0x180, 75),
    # SNF: Die 1 (nodes 76-79), PA[9]=1
    ("SNF", MAIN_MEM_MIN, MAIN_MEM_MAX, SNF_MASK, 0x200, 76),
    ("SNF", MAIN_MEM_MIN, MAIN_MEM_MAX, SNF_MASK, 0x280, 77),
    ("SNF", MAIN_MEM_MIN, MAIN_MEM_MAX, SNF_MASK, 0x300, 78),
    ("SNF", MAIN_MEM_MIN, MAIN_MEM_MAX, SNF_MASK, 0x380, 79),

    # AAN: Die 0 (nodes 64-67), proxy remote addr (Die 1 PA[9]=1)
    ("AAN", MAIN_MEM_MIN, MAIN_MEM_MAX, AAN_MASK, 0x200, 64),
    ("AAN", MAIN_MEM_MIN, MAIN_MEM_MAX, AAN_MASK, 0x280, 65),
    ("AAN", MAIN_MEM_MIN, MAIN_MEM_MAX, AAN_MASK, 0x300, 66),
    ("AAN", MAIN_MEM_MIN, MAIN_MEM_MAX, AAN_MASK, 0x380, 67),
    # AAN: Die 1 (nodes 68-71), proxy remote addr (Die 0 PA[9]=0)
    ("AAN", MAIN_MEM_MIN, MAIN_MEM_MAX, AAN_MASK, 0x000, 68),
    ("AAN", MAIN_MEM_MIN, MAIN_MEM_MAX, AAN_MASK, 0x080, 69),
    ("AAN", MAIN_MEM_MIN, MAIN_MEM_MAX, AAN_MASK, 0x100, 70),
    ("AAN", MAIN_MEM_MIN, MAIN_MEM_MAX, AAN_MASK, 0x180, 71),
]

class NoC_Params(CHI_config.NoC_Params):
    num_rows = 4
    num_cols = 8
    # D2D cross-chiplet links between col 3 and col 4
    cross_links = [
        (3, 4), (4, 3),
        (11, 12), (12, 11),
        (19, 20), (20, 19),
        (27, 28), (28, 27),
    ]
    cross_link_latency = 100


# Chiplet 0 core+LLC columns: col 1-2
# Chiplet 1 core+LLC columns: col 5-6
_core_routers = [
    # row 0
    1, 2, 5, 6,
    # row 1
    9, 10, 13, 14,
    # row 2
    17, 18, 21, 22,
    # row 3
    25, 26, 29, 30,
]

# HNF routers grouped by chip to align with SNF locality (PA[9:7]).
# Chiplet 0 HNFs first (col 1-2), then Chiplet 1 HNFs (col 5-6).
_hnf_routers = [
    # Chiplet 0: col 1-2, rows 0-3
    1, 2,
    9, 10,
    17, 18,
    25, 26,
    # Chiplet 1: col 5-6, rows 0-3
    5, 6,
    13, 14,
    21, 22,
    29, 30,
]

# Memory columns: col 0 (Chiplet 0) and col 7 (Chiplet 1)
_mem_routers = [
    0, 8, 16, 24,    # Chiplet 0
    7, 15, 23, 31,   # Chiplet 1
]

_aan_routers = [
    3, 11, 19, 27,   # Chiplet 0 requester-side D2D boundary
    4, 12, 20, 28,   # Chiplet 1 requester-side D2D boundary
]


class CHI_RNF(CHI_config.CHI_RNF):
    """Cores distributed across col 1-2 and col 5-6 routers (round-robin)."""
    class NoC_Params(CHI_config.CHI_RNF.NoC_Params):
        router_list = _core_routers


class CHI_HNF(CHI_config.CHI_HNF):
    """LLC/directory slices distributed across col 1-2 and col 5-6 routers."""
    class NoC_Params(CHI_config.CHI_HNF.NoC_Params):
        router_list = _hnf_routers


class CHI_AAN(CHI_config.CHI_AAN):
    """AMO aggregation nodes at requester-side D2D boundary routers."""
    class NoC_Params(CHI_config.CHI_AAN.NoC_Params):
        router_list = _aan_routers


class CHI_MN(CHI_config.CHI_MN):
    """Misc node on Chiplet 0, col 0, row 0."""
    class NoC_Params(CHI_config.CHI_MN.NoC_Params):
        router_list = [0]


class CHI_SNF_MainMem(CHI_config.CHI_SNF_MainMem):
    """8 DDR5 channels: 4 on col 0 (Chiplet 0), 4 on col 7 (Chiplet 1)."""
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
