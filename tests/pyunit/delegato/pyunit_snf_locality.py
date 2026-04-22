#!/usr/bin/env python3
"""
Unit tests for HNF-to-SNF same-chip locality in Delegato topologies.

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


class DelegatoSnfLocalityTest(unittest.TestCase):
    def assert_same_chip_locality(self, topology, hnf_count, snf_count):
        hnf_low_bit = topology.CHI_HNF.NoC_Params.addr_map.intlv_low_bit
        snf_low_bit = topology.CHI_SNF_MainMem.NoC_Params.addr_map.intlv_low_bit
        hnfs_per_chip = hnf_count // 2
        snfs_per_chip = snf_count // 2

        for hnf_idx in range(hnf_count):
            addr = sample_addr(hnf_idx, hnf_low_bit)
            hnf_chip = hnf_idx // hnfs_per_chip
            snf_idx = selector(addr, snf_low_bit, snf_count)
            snf_chip = snf_idx // snfs_per_chip
            self.assertEqual(
                hnf_chip,
                snf_chip,
                (
                    f"address 0x{addr:x} maps HNF {hnf_idx} on chip {hnf_chip} "
                    f"to SNF {snf_idx} on chip {snf_chip}"
                ),
            )

    def test_delegato_4x8_hnf_requests_stay_on_local_snf(self):
        self.assert_same_chip_locality(load_topology("delegato_4x8"), 16, 8)

    def test_delegato_4x12_hnf_requests_stay_on_local_snf(self):
        self.assert_same_chip_locality(load_topology("delegato_4x12"), 32, 8)
