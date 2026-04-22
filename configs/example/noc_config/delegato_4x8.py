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

# Memory columns: col 0 (Chiplet 0) and col 7 (Chiplet 1)
_mem_routers = [
    0, 8, 16, 24,    # Chiplet 0
    7, 15, 23, 31,   # Chiplet 1
]


class CHI_RNF(CHI_config.CHI_RNF):
    """Cores distributed across col 1-2 and col 5-6 routers (round-robin)."""
    class NoC_Params(CHI_config.CHI_RNF.NoC_Params):
        router_list = _core_routers


class CHI_HNF(CHI_config.CHI_HNF):
    """LLC/directory slices distributed across col 1-2 and col 5-6 routers."""
    class NoC_Params(CHI_config.CHI_HNF.NoC_Params):
        router_list = _core_routers
        # 16 HNFs: keep cacheline-granularity striping (64B -> PA[9:6]).
        addr_map = CHI_config.AddrMap(intlv_low_bit=6, xor_low_bit=0)


class CHI_MN(CHI_config.CHI_MN):
    """Misc node on Chiplet 0, col 0, row 0."""
    class NoC_Params(CHI_config.CHI_MN.NoC_Params):
        router_list = [0]


class CHI_SNF_MainMem(CHI_config.CHI_SNF_MainMem):
    """8 DDR5 channels: 4 on col 0 (Chiplet 0), 4 on col 7 (Chiplet 1)."""
    class NoC_Params(CHI_config.CHI_SNF_MainMem.NoC_Params):
        router_list = _mem_routers
        # 8 SNFs: 256B striping with no XOR hash (PA[10:8]) keeps each die's
        # HNFs on that die's local SNFs.
        addr_map = CHI_config.AddrMap(intlv_low_bit=8, xor_low_bit=0)


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
