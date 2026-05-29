# Copyright (c) 2026
# All rights reserved.

"""Delegato custom route-table helpers."""

import importlib.machinery
import os
import sys
import types


def _fatal(message, *args):
    try:
        import m5
    except ImportError as exc:
        text = message % args if args else message
        raise ValueError(text) from exc
    m5.fatal(message, *args)


def _route_int(text, entry):
    try:
        return int(text, 0)
    except ValueError as exc:
        raise ValueError(
            "invalid custom route integer '%s' in '%s'" % (text, entry)
        ) from exc


def _route_int_or_fatal(text, entry):
    try:
        return _route_int(text, entry)
    except ValueError as exc:
        _fatal(str(exc))


def read_route_config(file):
    """Read a NoC config file as a module."""

    config_dir = os.path.dirname(os.path.abspath(file))
    sys.path.insert(0, config_dir)
    loader = importlib.machinery.SourceFileLoader(
        "delegato_route_config", file
    )
    route_config = types.ModuleType(loader.name)
    try:
        loader.exec_module(route_config)
    finally:
        sys.path.remove(config_dir)
    return route_config


def configure_system_route_helpers(options, chi_config, *, single_die=False):
    route_config = (
        read_route_config(chi_config)
        if isinstance(chi_config, str) else chi_config
    )
    single_die = bool(getattr(route_config, "SINGLE_DIE", single_die))

    def create_hnf_ranges(CHI_HNF, sysranges, hnf_list):
        return create_hnf_ranges_from_route_table(
            route_config, CHI_HNF, sysranges, hnf_list
        )

    def configure_route_table(ruby_system, system):
        return configure_custom_route_table(
            ruby_system, system, route_config, single_die=single_die
        )

    options._delegato_route_config = route_config
    options._create_custom_hnf_ranges = create_hnf_ranges
    options._configure_custom_route_table = configure_route_table
    options._custom_route_ranges_by_version = custom_route_ranges_by_version
    return route_config


class RouteTableBuilder:
    def __init__(self, ruby_system):
        self.ruby = ruby_system
        self._node_entries = []
        self._route_entries = []
        self._next_node_id = 0

    def add_node(self, cntrl, role, chiplet, node_id=None):
        if node_id is None:
            node_id = self._next_node_id
        self._next_node_id = max(self._next_node_id, node_id + 1)
        machine_type = _machine_type(cntrl, role)
        version = int(cntrl.version)
        self._node_entries.append(
            "%d %s %d %s %d"
            % (node_id, machine_type, version, role, int(chiplet))
        )
        return node_id

    def add_entry(self, table, min_addr, max_addr, mask, compare, node_id):
        self._route_entries.append(
            "%s %#x %#x %#x %#x %d"
            % (
                table,
                int(min_addr),
                int(max_addr),
                int(mask),
                int(compare),
                int(node_id),
            )
        )

    def install(self):
        self.ruby.route_node_entries = self._node_entries
        self.ruby.route_table_entries = self._route_entries


def _machine_type(cntrl, role):
    if role == "MN":
        return "MiscNode"
    name = cntrl.__class__.__name__
    if "Memory_Controller" in name:
        return "Memory"
    return "Cache"


def _controllers(nodes):
    return [
        cntrl
        for node in nodes
        for cntrl in node.getAllControllers()
    ]


def configure_custom_route_table(
    ruby_system,
    system,
    route_config,
    *,
    single_die=False,
):
    """Install node metadata and address route tables on a RubySystem."""

    node_id_base = getattr(route_config, "NODE_ID_BASE", None)
    route_table = getattr(route_config, "route_table", None)
    if node_id_base is None or route_table is None:
        _fatal(
            "--enable-custom-route-table requires noc_config to expose "
            "NODE_ID_BASE and route_table"
        )

    builder = RouteTableBuilder(ruby_system)
    optional_route_node_ids = set(
        getattr(route_config, "OPTIONAL_ROUTE_NODE_IDS", ())
    )
    configured_node_ids = set()

    cpus = [
        cpu
        for rnf in getattr(ruby_system, "rnf", [])
        for cpu in getattr(rnf, "_cpus", [])
    ]
    l1i_controllers = [
        getattr(cpu, "l1i")
        for cpu in cpus
        if getattr(cpu, "l1i", None) is not None
    ]
    l1d_controllers = [
        getattr(cpu, "l1d")
        for cpu in cpus
        if getattr(cpu, "l1d", None) is not None
    ]
    l2_controllers = [
        getattr(cpu, "l2")
        for cpu in cpus
        if hasattr(cpu, "l2")
    ]
    hnf_controllers = _controllers(getattr(ruby_system, "hnf", []))
    aan_controllers = _controllers(getattr(ruby_system, "aan", []))
    snf_controllers = _controllers(getattr(ruby_system, "snf", []))
    rom_snf_controllers = _controllers(getattr(ruby_system, "rom_snf", []))
    mn_controllers = _controllers(getattr(ruby_system, "mn", []))
    dma_controllers = _controllers(getattr(ruby_system, "dma_rni", []))
    io_controllers = _controllers([getattr(ruby_system, "io_rni")]) \
        if hasattr(ruby_system, "io_rni") else []

    if not hnf_controllers:
        _fatal("custom route table requires HNF nodes")

    def chiplet_by_index(idx, per_chiplet):
        return 0 if single_die else min(1, idx // per_chiplet)

    def add_indexed(node_key, controllers, chiplet_fn, role=None):
        if role is None:
            role = node_key
        base = node_id_base[node_key]
        for idx, cntrl in enumerate(controllers):
            node_id = base + idx
            builder.add_node(cntrl, role, chiplet_fn(idx), node_id)
            configured_node_ids.add(node_id)

    add_indexed(
        "L1I",
        l1i_controllers,
        lambda idx: chiplet_by_index(idx, max(1, len(cpus) // 2)),
    )
    add_indexed(
        "L1D",
        l1d_controllers,
        lambda idx: chiplet_by_index(idx, max(1, len(cpus) // 2)),
    )
    add_indexed(
        "RNF",
        l2_controllers,
        lambda idx: chiplet_by_index(idx, max(1, len(l2_controllers) // 2)),
    )
    add_indexed(
        "HNF",
        hnf_controllers,
        lambda idx: chiplet_by_index(
            idx,
            len(hnf_controllers) if single_die
            else max(1, len(hnf_controllers) // 2),
        ),
    )
    add_indexed(
        "AAN",
        aan_controllers,
        lambda idx: chiplet_by_index(
            idx,
            len(aan_controllers) if single_die
            else max(1, len(aan_controllers) // 2),
        ),
    )
    add_indexed(
        "SNF",
        snf_controllers,
        lambda idx: chiplet_by_index(
            idx,
            max(1, len(snf_controllers) // 2),
        ),
    )
    add_indexed("BOOT_SNF", rom_snf_controllers, lambda idx: 0, role="SNF")
    add_indexed("MN", mn_controllers, lambda idx: 0)
    add_indexed("RNI", dma_controllers + io_controllers, lambda idx: 0)

    for table, min_addr, max_addr, mask, compare, node_id in route_table:
        if node_id not in configured_node_ids:
            if node_id in optional_route_node_ids:
                continue
            _fatal(
                "route table %s targets missing node id %d",
                table,
                node_id,
            )
        builder.add_entry(table, min_addr, max_addr, mask, compare, node_id)

    builder.install()


def _custom_route_masks(mask, compare, entry):
    if compare & ~mask:
        _fatal("custom route compare has bits outside mask in '%s'", entry)

    masks = []
    intlv_match = 0
    bit_pos = 0
    bit = 1
    while bit <= mask:
        if mask & bit:
            masks.append(bit)
            if compare & bit:
                intlv_match |= 1 << bit_pos
            bit_pos += 1
        bit <<= 1
    return masks, intlv_match


def custom_route_mask_high_bit(mask):
    return mask.bit_length() - 1 if mask else -1


def custom_route_addr_range(min_addr, max_addr, mask, compare, entry):
    from m5.objects import AddrRange

    masks, intlv_match = _custom_route_masks(mask, compare, entry)
    return AddrRange(
        start=min_addr,
        end=max_addr,
        masks=masks,
        intlvMatch=intlv_match,
    )


def _intersect_custom_route_range(
    min_addr, max_addr, mask, compare, system_ranges, entry
):
    ranges = []
    for sys_range in system_ranges:
        start = max(min_addr, int(sys_range.start))
        end = min(max_addr, int(sys_range.end))
        if start < end:
            ranges.append(
                custom_route_addr_range(start, end, mask, compare, entry)
            )
    return ranges


def _custom_route_nodes(ruby):
    nodes = {}
    for raw in getattr(ruby, "route_node_entries", []):
        parts = raw.split()
        if len(parts) != 5:
            _fatal(
                "custom route node entry must be "
                "'node-id machine-type machine-version role chiplet-id': %s",
                raw,
            )
        node_id = _route_int_or_fatal(parts[0], raw)
        nodes[node_id] = {
            "machine_type": parts[1],
            "version": _route_int_or_fatal(parts[2], raw),
            "role": parts[3],
            "chiplet": _route_int_or_fatal(parts[4], raw),
        }
    return nodes


def custom_route_ranges_by_version(
    ruby, table, machine_type, system_ranges
):
    nodes = _custom_route_nodes(ruby)
    ranges = {}
    for raw in getattr(ruby, "route_table_entries", []):
        parts = raw.split()
        if len(parts) != 6:
            _fatal(
                "custom route table entry must be "
                "'table min max mask compare destination-node-id': %s",
                raw,
            )
        if parts[0] != table:
            continue

        node_id = _route_int_or_fatal(parts[5], raw)
        node = nodes.get(node_id)
        if node is None:
            _fatal("custom route table targets unknown node id %d", node_id)
        if node["machine_type"] != machine_type or node["role"] != table:
            continue

        min_addr = _route_int_or_fatal(parts[1], raw)
        max_addr = _route_int_or_fatal(parts[2], raw)
        mask = _route_int_or_fatal(parts[3], raw)
        compare = _route_int_or_fatal(parts[4], raw)
        if min_addr >= max_addr:
            _fatal("custom route table entry has invalid range in '%s'", raw)

        version_ranges = ranges.setdefault(node["version"], [])
        version_ranges.extend(
            _intersect_custom_route_range(
                min_addr, max_addr, mask, compare, system_ranges, raw
            )
        )
    return ranges


def create_hnf_ranges_from_route_table(
    route_config, CHI_HNF, sysranges, hnf_list
):
    route_table = getattr(route_config, "route_table", None)
    node_id_base = getattr(route_config, "NODE_ID_BASE", None)
    if route_table is None or node_id_base is None:
        _fatal(
            "--enable-custom-route-table requires noc_config to expose "
            "route_table and NODE_ID_BASE"
        )
    if "HNF" not in node_id_base:
        _fatal("custom route NODE_ID_BASE must contain HNF")

    hnf_base = int(node_id_base["HNF"])
    ranges_by_hnf = {}
    high_bit_by_hnf = {}
    for table, min_addr, max_addr, mask, compare, node_id in route_table:
        if table != "HNF":
            continue

        hnf_idx = int(node_id) - hnf_base
        if hnf_idx not in hnf_list:
            _fatal("custom HNF route targets invalid HNF index %d", hnf_idx)
        high_bit = custom_route_mask_high_bit(int(mask))
        if high_bit < 0:
            _fatal("custom HNF route requires a non-zero mask")

        entry = "%s %#x %#x %#x %#x %d" % (
            table,
            int(min_addr),
            int(max_addr),
            int(mask),
            int(compare),
            int(node_id),
        )
        for sysrange in sysranges:
            start = max(int(min_addr), int(sysrange.start))
            end = min(int(max_addr), int(sysrange.end))
            if start < end:
                ranges_by_hnf.setdefault(hnf_idx, []).append(
                    custom_route_addr_range(
                        start, end, int(mask), int(compare), entry
                    )
                )
                high_bit_by_hnf[hnf_idx] = max(
                    high_bit_by_hnf.get(hnf_idx, -1), high_bit
                )

    for hnf_idx in hnf_list:
        ranges = ranges_by_hnf.get(hnf_idx)
        if not ranges:
            _fatal("custom route table has no HNF ranges for index %d",
                   hnf_idx)
        CHI_HNF.setAddrRanges(
            hnf_idx, ranges, high_bit_by_hnf[hnf_idx] + 1
        )


def cache_chiplet_map_from_route_nodes(ruby_system):
    entries = getattr(ruby_system, "route_node_entries", [])
    if not entries:
        raise ValueError("custom route node metadata is not configured")

    node_id_to_chiplet = []
    for raw in entries:
        parts = raw.split()
        if len(parts) != 5:
            raise ValueError(
                "route node entry must be "
                "'node-id machine-type machine-version role chiplet-id': "
                f"{raw}"
            )
        machine_type = parts[1]
        if machine_type != "Cache":
            continue

        version = _route_int(parts[2], raw)
        chiplet = _route_int(parts[4], raw)
        while len(node_id_to_chiplet) <= version:
            node_id_to_chiplet.append(-1)
        if node_id_to_chiplet[version] not in (-1, chiplet):
            raise ValueError(
                "conflicting chiplet mapping for Cache version "
                f"{version}: {node_id_to_chiplet[version]} vs {chiplet}"
            )
        node_id_to_chiplet[version] = chiplet

    return node_id_to_chiplet
