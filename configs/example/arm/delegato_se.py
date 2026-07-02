# Copyright (c) 2026
# All rights reserved.

"""Delegato CHI syscall-emulation workload runner."""

import argparse
import math
import os
import sys

import m5
from m5.objects import *

m5.util.addToPath("../..")

from common import ObjectList
from common.cores.arm import HPI, O3_ARM_v7a
from ruby import Ruby
from amo_policy import (
    add_amo_policy_args,
    resolve_amo_policy,
)
from delegato_routing_helper import (
    cache_chiplet_map_from_route_nodes,
    configure_system_route_helpers,
)


class DelegatoO3CPU(O3_ARM_v7a.O3_ARM_v7a_3):
    """O3 CPU configuration matching Delegato paper Table 3."""

    fetchWidth = 8
    decodeWidth = 8
    renameWidth = 8
    commitWidth = 8
    squashWidth = 8

    dispatchWidth = 13
    issueWidth = 13
    wbWidth = 13

    numROBEntries = 224
    LQEntries = 76
    SQEntries = 58
    numIQEntries = 97
    numPhysIntRegs = 280
    numPhysFloatRegs = 256
    numPhysVecRegs = 256
    fetchBufferSize = 64


cpu_types = {
    "timing": TimingSimpleCPU,
    "minor": MinorCPU,
    "hpi": HPI.HPI,
    "o3": DelegatoO3CPU,
}


def _set_cpu_heartbeat(cpus, heartbeat_insts):
    for cpu in cpus:
        cpu.heartbeat_insts = heartbeat_insts


def _parse_redirects(raw_redirects):
    redirects = []
    for raw in raw_redirects:
        if "=" not in raw:
            raise ValueError(f"redirect must be APP=HOST[:HOST...] form: {raw}")
        app_path, host_paths = raw.split("=", 1)
        hosts = [p for p in host_paths.split(":") if p]
        if not app_path or not hosts:
            raise ValueError(f"redirect must be APP=HOST[:HOST...] form: {raw}")
        redirects.append(RedirectPath(app_path=app_path, host_paths=hosts))
    return redirects


def _append_redirect_paths(system, redirects):
    for idx, redirect in enumerate(redirects):
        setattr(system, f"se_redirect{idx}", redirect)
    system.redirect_paths = list(system.redirect_paths) + redirects


def _configure_topology(args, parser):
    if args.num_cpus not in (4, 16, 32):
        parser.error("--num-cpus only supports 4, 16, or 32")
    if args.heartbeat_insts < 0:
        parser.error("--heartbeat-insts must be a non-negative integer")
    policy = resolve_amo_policy(
        args.amo_policy,
        args.l1d_amo_policy,
        args.aan_amo_policy,
        args.hnf_amo_policy,
    )
    if policy.aan_policy != "bypass" and args.num_cpus not in (16, 32):
        parser.error("AAN policies require --num-cpus 16 or 32")

    if args.num_cpus == 4:
        noc_name = "delegato_single_2x4.py"
        args.num_l3caches = 4
        args.num_dirs = 2
        args.mem_channels = 2
        print("Using single-die 2x4 mesh (4-core fast-test config)")
    elif args.num_cpus == 16:
        noc_name = "delegato_4x8.py"
        args.num_l3caches = 16
        args.num_dirs = 8
        args.mem_channels = 8
        print("Using dual-chiplet 4x8 mesh (16-core default config)")
    else:
        noc_name = "delegato_4x12.py"
        args.num_l3caches = 32
        args.num_dirs = 8
        args.mem_channels = 8
        print("Using dual-chiplet 4x12 mesh (32-core full config)")

    args.topology = "CustomMesh"
    args.chi_config = os.path.join(
        os.path.dirname(__file__), "..", "noc_config", noc_name
    )
    args.network = "garnet"
    args.ruby_clock = "2GHz"
    args.enable_custom_route_table = True
    configure_system_route_helpers(
        args, args.chi_config, single_die=(args.num_cpus == 4)
    )


def _apply_amo_policy(system, args):
    policy = resolve_amo_policy(
        args.amo_policy,
        args.l1d_amo_policy,
        args.aan_amo_policy,
        args.hnf_amo_policy,
    )
    print(
        "AMO policy: %s  (L1D=%s, AAN=%s, HN-F=%s)"
        % (
            policy.top_policy,
            policy.l1d_policy,
            policy.aan_policy,
            policy.hnf_policy,
        )
    )

    block_size_bits = int(math.log(args.cacheline_size, 2))
    if (1 << block_size_bits) != args.cacheline_size:
        m5.fatal("--cacheline_size must be a power of 2")

    try:
        node_id_to_chiplet = cache_chiplet_map_from_route_nodes(system.ruby)
    except ValueError as exc:
        m5.fatal(str(exc))

    for cpu in system.cpu:
        cpu.l1d.l1d_amo_policy = policy.l1d_policy_code
        cpu.l1d.hnf_amo_policy = policy.hnf_policy_code
        cpu.l1d.delegato_rt_enabled = policy.delegato_rt_enabled
        cpu.l1d.cache_block_size_bits = block_size_bits
        cpu.l1d.delegato_rt_entries = 128
        cpu.l1d.delegato_rt_assoc = 2
        if hasattr(cpu, "l2"):
            cpu.l2.hnf_amo_policy = policy.hnf_policy_code
            cpu.l2.cache_block_size_bits = block_size_bits

    for hnf in system.ruby.hnf:
        for cntrl in hnf.getAllControllers():
            version = int(cntrl.version)
            if (
                version >= len(node_id_to_chiplet) or
                node_id_to_chiplet[version] < 0
            ):
                m5.fatal(
                    "custom route node metadata has no HNF Cache version %d",
                    version,
                )
            cntrl.hnf_amo_policy = policy.hnf_policy_code
            cntrl.delegato_pt_entries = 128
            cntrl.delegato_pt_assoc = 2
            cntrl.cache_block_size_bits = block_size_bits
            cntrl.hnf_chiplet_id = node_id_to_chiplet[version]

    for aan in getattr(system.ruby, "aan", []):
        for cntrl in aan.getAllControllers():
            cntrl.aan_amo_policy = policy.aan_policy_code
            cntrl.hnf_amo_policy = policy.hnf_policy_code
            cntrl.cache_block_size_bits = block_size_bits
            cntrl.aan_bat_entries = 128
            cntrl.aan_bat_assoc = 2

    for hnf in system.ruby.hnf:
        for cntrl in hnf.getAllControllers():
            cntrl.node_id_to_chiplet = node_id_to_chiplet


def _create_process(args):
    process = Process(pid=100)
    process.executable = args.cmd
    process.cmd = args.arg if args.arg else [args.cmd]
    process.cwd = args.cwd
    process.gid = os.getgid()
    process.env = args.env
    process.output = os.path.join(m5.options.outdir, "stdout.txt")
    process.errout = os.path.join(m5.options.outdir, "stderr.txt")
    if args.input:
        process.input = args.input
    return process


def create(args):
    cpu_class = cpu_types[args.cpu]
    cpu_class.numThreads = 1

    if args.interp_dir:
        from m5.core import setInterpDir

        setInterpDir(args.interp_dir)
        args.interp_dir = None

    system = System(
        cpu=[cpu_class(cpu_id=i) for i in range(args.num_cpus)],
        mem_mode=cpu_class.memory_mode(),
        mem_ranges=[AddrRange(start=0x200000000, size=args.mem_size)],
        cache_line_size=args.cacheline_size,
    )

    system.voltage_domain = VoltageDomain(voltage=args.sys_voltage)
    system.clk_domain = SrcClockDomain(
        clock=args.sys_clock, voltage_domain=system.voltage_domain
    )
    system.cpu_voltage_domain = VoltageDomain()
    system.cpu_clk_domain = SrcClockDomain(
        clock=args.cpu_freq, voltage_domain=system.cpu_voltage_domain
    )
    for cpu in system.cpu:
        cpu.clk_domain = system.cpu_clk_domain
        cpu.createInterruptController()

    _set_cpu_heartbeat(system.cpu, args.heartbeat_insts)

    process = _create_process(args)
    system.workload = SEWorkload.init_compatible(
        args.cmd,
        remote_gdb_port=0,
        wait_for_remote_gdb=False,
    )

    for cpu in system.cpu:
        cpu.workload = process
        cpu.createThreads()

    Ruby.create_system(args, False, system, cpus=system.cpu)
    _append_redirect_paths(system, _parse_redirects(args.redirect))
    system.ruby.clk_domain = SrcClockDomain(
        clock=args.ruby_clock, voltage_domain=system.voltage_domain
    )

    for i, cpu in enumerate(system.cpu):
        system.ruby._cpu_ports[i].connectCpuPorts(cpu)

    _apply_amo_policy(system, args)

    return system


def run(args):
    print("WORKLOAD BEGIN cmd=%s" % " ".join(args.arg if args.arg else [args.cmd]))
    sys.stdout.flush()

    m5.stats.reset()
    event = m5.simulate()
    m5.stats.dump()

    exit_msg = event.getCause()
    exit_code = event.getCode()
    if exit_msg == "exiting with last active thread context" and exit_code == 0:
        status = "exited"
    else:
        status = str(exit_code)

    print("%s @ tick %d (code=%d)" % (exit_msg, m5.curTick(), exit_code))
    print("WORKLOAD END status=%s" % status)
    sys.stdout.flush()
    sys.exit(exit_code)


def main():
    parser = argparse.ArgumentParser(
        description="Delegato CHI syscall-emulation workload runner"
    )
    parser.add_argument("--cmd", required=True, help="Path to workload ELF")
    parser.add_argument("--arg", action="append", default=[],
                        help="Workload argv token; pass once per argument")
    parser.add_argument("--input", type=str, default=None,
                        help="Path to host stdin file")
    parser.add_argument("--cwd", type=str, default=os.getcwd())
    parser.add_argument("--env", action="append", default=[],
                        help="Environment assignment; pass once per variable")
    parser.add_argument("--redirect", action="append", default=[],
                        help="Path redirect as APP=HOST[:HOST...]")
    parser.add_argument("--interp-dir", type=str, default=None,
                        help="Host parent directory prepended to ELF interpreter path")

    parser.add_argument("--cpu", choices=list(cpu_types.keys()), default="minor")
    parser.add_argument("--cpu-freq", type=str, default="3GHz")
    parser.add_argument("--sys-clock", type=str, default="1GHz")
    parser.add_argument("--sys-voltage", type=str, default="1.0V")
    parser.add_argument("-n", "--num-cpus", type=int, default=16)
    parser.add_argument("--heartbeat-insts", type=int, default=0)

    parser.add_argument("--mem-type", default="DDR5_4400_4x8",
                        choices=ObjectList.mem_list.get_names())
    parser.add_argument("--mem-channels", type=int, default=8)
    parser.add_argument("--mem-ranks", type=int, default=None)
    parser.add_argument("--mem-size", type=str, default="8GiB")
    parser.add_argument("--enable-dram-powerdown", action="store_true")
    parser.add_argument("--mem-channels-intlv", type=int, default=0)

    parser.add_argument("--num-dirs", type=int, default=8)
    parser.add_argument("--num-l2caches", type=int, default=1)
    parser.add_argument("--num-l3caches", type=int, default=32)
    parser.add_argument("--l1d_size", type=str, default="64KiB")
    parser.add_argument("--l1i_size", type=str, default="64KiB")
    parser.add_argument("--l2_size", type=str, default="1MiB")
    parser.add_argument("--l3_size", type=str, default="1MiB")
    parser.add_argument("--l1d_assoc", type=int, default=4)
    parser.add_argument("--l1i_assoc", type=int, default=4)
    parser.add_argument("--l2_assoc", type=int, default=8)
    parser.add_argument("--l3_assoc", type=int, default=16)
    parser.add_argument("--cacheline_size", type=int, default=64)

    Ruby.define_options(parser)

    add_amo_policy_args(parser)

    args = parser.parse_args()

    if not os.path.isfile(args.cmd):
        parser.error(f"--cmd not found: {args.cmd}")
    if not args.arg:
        args.arg = [args.cmd]
    if args.arg[0] != args.cmd:
        parser.error("first --arg must be the workload executable path")
    if args.input and not os.path.isfile(args.input):
        parser.error(f"--input not found: {args.input}")
    if args.interp_dir and not os.path.isdir(args.interp_dir):
        parser.error(f"--interp-dir not found: {args.interp_dir}")
    if args.cpu not in cpu_types:
        parser.error("--cpu must be timing, minor, hpi, or o3")

    _configure_topology(args, parser)

    root = Root(full_system=False)
    root.system = create(args)

    m5.instantiate()
    run(args)


if __name__ == "__m5_main__":
    main()
