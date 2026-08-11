#!/usr/bin/env python3
"""Unit tests for D2D SerDes flow-control topology configuration."""

import importlib
import pathlib
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch


REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
CONFIGS_ROOT = REPO_ROOT / "configs"

if str(CONFIGS_ROOT) not in sys.path:
    sys.path.insert(0, str(CONFIGS_ROOT))

from topologies.CustomMesh import CustomMesh

network_config = importlib.import_module("network.Network")


class FakeLink:
    def __init__(self, **kwargs):
        self.network_link = SimpleNamespace(buffer_depth=0)
        self.serdes_vc_buffer_depth = 0
        self.__dict__.update(kwargs)


class FakeBridge:
    def __init__(self, buffer_depth=0, **kwargs):
        self.buffer_depth = buffer_depth
        self.__dict__.update(kwargs)


def build_mesh(
    cross_width,
    mesh_width,
    cross_link_latency=400,
    num_rows=1,
    num_columns=2,
    cross_links=None,
):
    if cross_links is None:
        cross_links = {(0, 1), (1, 0)}

    mesh = object.__new__(CustomMesh)
    mesh._routers = [object() for _ in range(num_rows * num_columns)]
    mesh._int_links = []
    mesh._link_count = 0
    mesh._makeMesh(
        FakeLink,
        link_latency=1,
        num_rows=num_rows,
        num_columns=num_columns,
        cross_links=cross_links,
        cross_link_latency=cross_link_latency,
        cross_link_width=cross_width,
        mesh_link_width=mesh_width,
    )
    return mesh._int_links


class D2DSerDesFlowControlConfigTest(unittest.TestCase):
    def test_serdes_d2d_depth_covers_credit_round_trip(self):
        links = build_mesh(64, 32)

        self.assertEqual(len(links), 2)
        for link in links:
            self.assertTrue(link.src_serdes)
            self.assertTrue(link.dst_serdes)
            self.assertEqual(link.serdes_vc_buffer_depth, 802)

    def test_serdes_d2d_depth_tracks_cross_link_latency(self):
        links = build_mesh(64, 32, cross_link_latency=50)

        for link in links:
            self.assertEqual(link.serdes_vc_buffer_depth, 102)

    def test_equal_width_d2d_keeps_decoupling_disabled(self):
        links = build_mesh(32, 32)

        self.assertEqual(len(links), 2)
        for link in links:
            self.assertFalse(link.src_serdes)
            self.assertFalse(link.dst_serdes)
            self.assertEqual(link.serdes_vc_buffer_depth, 0)
            self.assertEqual(link.network_link.buffer_depth, 802)

    def test_intra_chiplet_links_keep_serdes_buffer_disabled(self):
        links = build_mesh(
            64,
            32,
            num_columns=3,
            cross_links={(0, 1), (1, 0)},
        )
        intra_chiplet_links = [link for link in links if link.latency == 1]

        self.assertEqual(len(intra_chiplet_links), 2)
        for link in intra_chiplet_links:
            self.assertEqual(link.serdes_vc_buffer_depth, 0)

    def test_internal_data_bridges_receive_serdes_depth(self):
        int_link = SimpleNamespace(
            network_link=object(),
            credit_link=object(),
            src_node=SimpleNamespace(width=32),
            dst_node=SimpleNamespace(width=32),
            serdes_vc_buffer_depth=802,
        )
        network = SimpleNamespace(int_links=[int_link], ext_links=[])
        options = SimpleNamespace(
            network="garnet",
            mesh_rows=1,
            vcs_per_vnet=4,
            link_width_bits=256,
            routing_algorithm=0,
            garnet_deadlock_threshold=50000,
            network_fault_model=False,
        )

        with patch.object(network_config, "NetworkBridge", FakeBridge):
            network_config.init_network(options, network, InterfaceClass=None)

        self.assertEqual(int_link.src_net_bridge.buffer_depth, 802)
        self.assertEqual(int_link.dst_net_bridge.buffer_depth, 802)
        self.assertEqual(int_link.src_cred_bridge.buffer_depth, 0)
        self.assertEqual(int_link.dst_cred_bridge.buffer_depth, 0)


if __name__ == "__main__":
    unittest.main()
