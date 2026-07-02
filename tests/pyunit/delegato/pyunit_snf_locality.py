#!/usr/bin/env python3
"""
Unit tests for HNF-to-SNF locality in Delegato topologies.

Note: This module requires gem5's pyunit test harness to run, as topology
configuration modules depend on gem5's m5 library. Do not run standalone.
"""

import importlib.util
import math
import pathlib
import sys
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
CONFIGS_ROOT = REPO_ROOT / "configs"
NOC_ROOT = CONFIGS_ROOT / "example" / "noc_config"

if str(CONFIGS_ROOT) not in sys.path:
    sys.path.insert(0, str(CONFIGS_ROOT))


def load_topology(name):
    spec = importlib.util.spec_from_file_location(name, NOC_ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def selector(addr, intlv_low_bit, num_nodes):
    intlv_bits = int(math.log(num_nodes, 2))
    return (addr >> intlv_low_bit) & ((1 << intlv_bits) - 1)


def sample_addr(hnf_idx, hnf_low_bit):
    """
    Generate one sample address for a given HNF slice.

    Returns the minimal address mapping to the specified HNF. One address per
    HNF is sufficient because interleaving is deterministic: any two addresses
    mapping to the same HNF slice will yield the same SNF by the selector
    function, so checking one address validates the entire slice mapping.
    """
    return hnf_idx << hnf_low_bit


def router_to_chip(router_id, num_cols):
    """
    Map a router ID to chip ID based on mesh column.

    In a 4×N mesh with dual chiplets:
    - Left half columns (0 to N/2-1) are chip 0
    - Right half columns (N/2 to N-1) are chip 1
    """
    col = router_id % num_cols
    return 0 if col < num_cols // 2 else 1


def router_to_row(router_id, num_cols):
    """Map a router ID to mesh row."""
    return router_id // num_cols


def _die_hnf_addr(hnf_idx, total_hnfs):
    """Generate sample address for HNF in die-aware scheme (PA[32]+PA[N:6])."""
    hnf_per_die = total_hnfs // 2
    die = hnf_idx // hnf_per_die
    pa_sel = hnf_idx % hnf_per_die
    return (die << 32) | (pa_sel << 6)


def _die_snf_idx(addr, snf_intlv_low_bit, snf_per_die):
    """Derive SNF index from address in die-aware scheme (PA[32]+PA[N:M])."""
    die = (addr >> 32) & 1
    sel_bits = int(math.log(snf_per_die, 2))
    row = (addr >> snf_intlv_low_bit) & ((1 << sel_bits) - 1)
    return die * snf_per_die + row


class DelegatoSnfLocalityTest(unittest.TestCase):
    def assert_same_chip_locality(self, topology, hnf_count, snf_count):
        hnf_low_bit = topology.CHI_HNF.NoC_Params.addr_map.intlv_low_bit
        snf_low_bit = topology.CHI_SNF_MainMem.NoC_Params.addr_map.intlv_low_bit
        hnf_router_list = topology.CHI_HNF.NoC_Params.router_list
        snf_router_list = topology.CHI_SNF_MainMem.NoC_Params.router_list
        num_cols = topology.NoC_Params.num_cols

        for hnf_idx in range(hnf_count):
            addr = sample_addr(hnf_idx, hnf_low_bit)
            hnf_router = hnf_router_list[hnf_idx]
            hnf_chip = router_to_chip(hnf_router, num_cols)
            snf_idx = selector(addr, snf_low_bit, snf_count)
            snf_router = snf_router_list[snf_idx]
            snf_chip = router_to_chip(snf_router, num_cols)
            self.assertEqual(
                hnf_chip,
                snf_chip,
                (
                    f"address 0x{addr:x} maps HNF {hnf_idx} (router {hnf_router}) "
                    f"on chip {hnf_chip} to SNF {snf_idx} (router {snf_router}) "
                    f"on chip {snf_chip}"
                ),
            )

    def assert_same_row_locality(self, topology, hnf_count, snf_count,
                                  snf_intlv_low_bit):
        """Verify HNF→SNF same-row locality for die-aware PA[32]+PA[N:M]."""
        hnf_router_list = topology.CHI_HNF.NoC_Params.router_list
        snf_router_list = topology.CHI_SNF_MainMem.NoC_Params.router_list
        num_cols = topology.NoC_Params.num_cols
        snf_per_die = snf_count // 2

        for hnf_idx in range(hnf_count):
            addr = _die_hnf_addr(hnf_idx, hnf_count)
            hnf_router = hnf_router_list[hnf_idx]
            hnf_row = router_to_row(hnf_router, num_cols)
            snf_idx = _die_snf_idx(addr, snf_intlv_low_bit, snf_per_die)
            snf_router = snf_router_list[snf_idx]
            snf_row = router_to_row(snf_router, num_cols)
            self.assertEqual(
                hnf_row,
                snf_row,
                (
                    f"address 0x{addr:x} maps HNF {hnf_idx} (router {hnf_router}, "
                    f"row {hnf_row}) to SNF {snf_idx} (router {snf_router}, "
                    f"row {snf_row})"
                ),
            )

    def test_delegato_4x8_hnf_requests_stay_on_local_snf(self):
        self.assert_same_row_locality(load_topology("delegato_4x8"), 16, 8, 7)

    def test_delegato_4x12_hnf_requests_stay_on_local_snf(self):
        self.assert_same_row_locality(load_topology("delegato_4x12"), 32, 8, 8)
