# Copyright (c) 2024 The Regents of the University of California
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
Delegato dual-chiplet full-system ARM simulation script.

Topology: 4×12 CustomMesh, 2 chiplets with D2D links.
Default parameters match the Delegato paper (MICRO '25, Table 3).

Modes of operation:
  Normal:       gem5.opt delegato_fs.py --kernel <Image> --initrd <cpio.gz> --cpu o3
  Fast-forward: gem5.opt delegato_fs.py ... --cpu o3 --fast-forward
                Boot with TimingSimpleCPU, switch to target CPU on ROI start.
  Save ckpt:    gem5.opt delegato_fs.py ... --save-checkpoint
                Boot with TimingSimpleCPU, save checkpoint on ROI start, exit.
  Restore:      gem5.opt delegato_fs.py ... --cpu o3 --restore <cpt_dir>
                Restore from checkpoint, switch to target CPU, run ROI.
  Bare-metal:   gem5.opt delegato_fs.py --bare-metal <elf> --cpu timing
                Boot bare-metal ELF directly (no Linux kernel).  The ELF
                must be linked at 0x80080000 (VExpress physical RAM + 512K).
                Uses boot.arm64 bootloader for DTB passing and spin-table
                secondary CPU boot.

The --fast-forward and --save-checkpoint modes require the guest to call
gem5 m5_checkpoint pseudo-instruction at ROI start (see test_init.c).
These modes are incompatible with --bare-metal.
"""

import argparse
import math
import os
import sys

import m5
from m5.objects import *
from m5.options import *
from m5.util import addToPath

m5.util.addToPath("../..")

import devices
from common import (
    ObjectList,
    Options,
    SysPaths,
)
from common.cores.arm import (
    HPI,
    O3_ARM_v7a,
)
from ruby import Ruby


# ─── O3 CPU tuned to Delegato paper Table 3 ─────────────────────────────

class DelegatoO3CPU(O3_ARM_v7a.O3_ARM_v7a_3):
    """O3 CPU configuration matching Delegato paper Table 3."""

    # Pipeline width (Table 3: fetch/decode/commit = 8)
    fetchWidth = 8
    decodeWidth = 8
    renameWidth = 8
    commitWidth = 8
    squashWidth = 8

    # Dispatch/Issue width (Table 3: 13)
    dispatchWidth = 13
    issueWidth = 13
    wbWidth = 13

    # ROB and queue sizes (Table 3)
    numROBEntries = 224
    LQEntries = 76
    SQEntries = 58

    # Scale IQ and physical registers for wider pipeline
    numIQEntries = 97
    numPhysIntRegs = 280
    numPhysFloatRegs = 256
    numPhysVecRegs = 256

    # Wider fetch buffer for 8-wide fetch
    fetchBufferSize = 64


cpu_types = {
    "atomic": NonCachingSimpleCPU,
    "timing": TimingSimpleCPU,
    "minor": MinorCPU,
    "hpi": HPI.HPI,
    "o3": DelegatoO3CPU,
}


# ─── System creation ─────────────────────────────────────────────────────

def create(args):
    """Create and configure the system."""

    # Determine boot CPU vs target CPU
    if args.fast_forward or args.save_checkpoint or args.restore:
        boot_cpu_class = TimingSimpleCPU
        target_cpu_class = cpu_types[args.cpu]
    else:
        boot_cpu_class = cpu_types[args.cpu]
        target_cpu_class = None

    mem_mode = boot_cpu_class.memory_mode()

    # Bare-metal vs Linux workload
    if args.bare_metal:
        system = devices.ArmRubySystem(
            args.mem_size,
            mem_mode=mem_mode,
            workload=ArmFsWorkload(object_file=args.bare_metal),
        )
    else:
        system = devices.ArmRubySystem(
            args.mem_size,
            mem_mode=mem_mode,
            workload=ArmFsLinux(object_file=args.kernel),
        )

    # CPU cluster (boot CPUs)
    system.cpu_cluster = [
        devices.ArmCpuCluster(
            system,
            args.num_cpus,
            args.cpu_freq,
            "1.0V",
            boot_cpu_class,
            None,  # L1I handled by Ruby/CHI
            None,  # L1D handled by Ruby/CHI
            None,  # L2 handled by Ruby/CHI
        )
    ]

    # Create switched-out target CPUs for fast-forward / restore
    # Follow gem5 standard pattern (configs/common/Simulation.py):
    #   - Do NOT call createInterruptController() — interrupts transfer
    #     via takeOverFrom() during m5.switchCpus()
    if target_cpu_class and target_cpu_class is not boot_cpu_class:
        switch_cpus = []
        for i, boot_cpu in enumerate(system.cpu_cluster[0].cpus):
            cpu = target_cpu_class(
                switched_out=True,
                cpu_id=boot_cpu.cpu_id,
                clk_domain=boot_cpu.clk_domain,
            )
            cpu.createThreads()
            switch_cpus.append(cpu)
        system.switch_cpus = switch_cpus

    # PCI VirtIO block device (optional, for disk image)
    if args.disk_image:
        image = CowDiskImage()
        image.child.image_file = args.disk_image
        system.pci_devices = [PciVirtIO(vio=VirtIOBlock(image=image))]
        for dev in system.pci_devices:
            system.attach_pci(dev)

    # Configure Ruby/CHI
    cpus = []
    for cluster in system.cpu_cluster:
        for cpu in cluster.cpus:
            cpus.append(cpu)

    Ruby.create_system(
        args,
        True,
        system,
        system.iobus,
        system._dma_ports,
        system.realview.bootmem,
        cpus,
    )

    block_size_bits = int(math.log(args.cacheline_size, 2))
    if (1 << block_size_bits) != args.cacheline_size:
        m5.fatal("--cacheline_size must be a power of 2")

    # AMO placement policy
    # Policy map:  l1d_policy_type  hnf_policy_type
    #   delegato        4                1           Delegato (C/D/M + RT + PT)
    #   dynamo          3                0           DynAMO Reuse-PN (AMT in L1D)
    #   unique-near     1                0           Unique-near baseline
    #   all-far         5                0           All-far (always to HN-F)
    policy_map = {
        "delegato":     (4, 1),
        "dynamo":       (3, 0),
        "unique-near":  (1, 0),
        "all-far":      (5, 0),
    }
    l1d_policy, hnf_policy = policy_map[args.amo_policy]
    print("AMO policy: %s  (L1D policy_type=%d, HN-F hnf_policy_type=%d)"
          % (args.amo_policy, l1d_policy, hnf_policy))

    for cpu in cpus:
        cpu.l1d.policy_type = l1d_policy
        cpu.l1d.cache_block_size_bits = block_size_bits
        cpu.l1d.delegato_rt_entries = 128
        cpu.l1d.delegato_rt_assoc = 2

    # Single-die: all cores in one chiplet; dual-chiplet: split evenly
    single_die = args.num_cpus <= 4
    cores_per_chiplet = args.num_cpus if single_die else max(1, args.num_cpus // 2)
    hnf_idx = 0
    for hnf in system.ruby.hnf:
        for cntrl in hnf.getAllControllers():
            cntrl.hnf_policy_type = hnf_policy
            cntrl.delegato_pt_entries = 128
            cntrl.delegato_pt_assoc = 2
            cntrl.cache_block_size_bits = block_size_bits
            cntrl.cores_per_chiplet = cores_per_chiplet
            cntrl.hnf_chiplet_id = 0 if hnf_idx < cores_per_chiplet else 1
            hnf_idx += 1

    system.ruby.clk_domain = SrcClockDomain(
        clock=args.ruby_clock, voltage_domain=system.voltage_domain
    )

    system.connect()
    system.realview.setupBootLoader(
        system, SysPaths.binary,
        boot_loader=[SysPaths.binary("boot.arm64")]
    )

    # Bare-metal: override load_addr_offset so the ELF is loaded at its
    # link address (0x80080000) unchanged.  setupBootLoader already set
    # dtb_addr (0x88000000) and cpu_release_addr (0x87FFFFF8) which
    # remain correct.
    if args.bare_metal:
        system.workload.load_addr_offset = 0

    # Keep the guest serial console in a dedicated outdir file so xmake can
    # tail it deterministically while still allowing interactive m5term use.
    system.terminal.outfile = "file"

    if args.dtb:
        system.workload.dtb_filename = args.dtb
    else:
        system.workload.dtb_filename = os.path.join(
            m5.options.outdir, "system.dtb"
        )
        system.generateDtb(system.workload.dtb_filename)

    if not args.bare_metal:
        # initrd support
        if args.initrd:
            system.workload.initrd_filename = args.initrd

        # Kernel command line
        kernel_cmd = [
            "earlycon=pl011,0x1c090000",
            "console=ttyAMA0",
            "loglevel=8",
            "lpj=19988480",
            "norandmaps",
            f"mem={args.mem_size}",
        ]
        if args.disk_image:
            kernel_cmd.append(f"root={args.root_device}")
            kernel_cmd.append("rw")
        if args.initrd:
            kernel_cmd.append("rdinit=/init")
        if args.fast_forward or args.save_checkpoint:
            kernel_cmd.append("gem5_m5ops=1")

        system.workload.command_line = " ".join(kernel_cmd)

    return system


# ─── Simulation loop ─────────────────────────────────────────────────────

def run(args, root):
    has_switch_cpus = hasattr(root.system, "switch_cpus")
    switched = False

    while True:
        event = m5.simulate()
        exit_msg = event.getCause()

        if exit_msg == "checkpoint":
            if args.save_checkpoint:
                cpt_dir = os.path.join(
                    m5.options.outdir, f"cpt.{m5.curTick()}"
                )
                m5.checkpoint(cpt_dir)
                print(f"Checkpoint saved: {cpt_dir}")
                sys.exit(0)

            elif has_switch_cpus and not switched:
                system = root.system
                n = len(system.switch_cpus)
                switch_cpu_list = [
                    (system.cpu_cluster[0].cpus[i], system.switch_cpus[i])
                    for i in range(n)
                ]
                print(f"Switching {n} CPUs to {args.cpu} @ tick {m5.curTick()}")
                m5.switchCpus(system, switch_cpu_list)
                switched = True
                print("CPU switch complete, ROI begins")
            else:
                # Normal checkpoint (no fast-forward)
                cpt_dir = os.path.join(
                    m5.options.outdir, f"cpt.{m5.curTick()}"
                )
                m5.checkpoint(cpt_dir)
                print(f"Checkpoint saved: {cpt_dir}")

        elif exit_msg == "m5_exit instruction encountered":
            print(f"Simulation complete @ tick {m5.curTick()}")
            break

        else:
            print(f"{exit_msg} @ tick {m5.curTick()}")
            break

    sys.exit(event.getCode())


# ─── Main ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Delegato dual-chiplet FS simulation"
    )

    # System
    parser.add_argument("--kernel", type=str, default=None,
                        help="Path to kernel Image (required unless --bare-metal)")
    parser.add_argument("--bare-metal", type=str, default=None,
                        help="Path to bare-metal ELF (linked at 0x80080000). "
                             "Mutually exclusive with --kernel/--initrd/--fast-forward.")
    parser.add_argument("--initrd", type=str, default=None,
                        help="Path to initrd/initramfs (cpio.gz)")
    parser.add_argument("--disk-image", type=str, default=None,
                        help="Path to disk image (optional)")
    parser.add_argument("--dtb", type=str, default=None,
                        help="DTB file (auto-generated if omitted)")
    parser.add_argument("--root-device", type=str, default="/dev/vda1",
                        help="Root device for disk image")

    # CPU
    parser.add_argument("--cpu", choices=list(cpu_types.keys()),
                        default="minor",
                        help="CPU model (default: minor)")
    parser.add_argument("--cpu-freq", type=str, default="3GHz",
                        help="CPU frequency (default: 3GHz)")
    parser.add_argument("-n", "--num-cpus", type=int, default=32,
                        help="Number of CPUs (default: 32)")

    # Fast-forward / Checkpoint
    parser.add_argument("--fast-forward", action="store_true",
                        help="Boot with TimingSimpleCPU, switch to --cpu "
                             "on ROI start (requires m5ops in guest)")
    parser.add_argument("--save-checkpoint", action="store_true",
                        help="Boot with TimingSimpleCPU, save checkpoint "
                             "on ROI start and exit")
    parser.add_argument("--restore", type=str, default=None,
                        help="Restore from checkpoint directory and switch "
                             "to --cpu type")

    # Memory
    parser.add_argument("--mem-type", default="DDR5_4400_4x8",
                        choices=ObjectList.mem_list.get_names(),
                        help="Memory type (default: DDR5_4400_4x8)")
    parser.add_argument("--mem-channels", type=int, default=8,
                        help="Memory channels (default: 8)")
    parser.add_argument("--mem-ranks", type=int, default=None)
    parser.add_argument("--mem-size", type=str, default="8GiB",
                        help="Physical memory size (default: 8GiB)")
    parser.add_argument("--enable-dram-powerdown", action="store_true")
    parser.add_argument("--mem-channels-intlv", type=int, default=0)

    # Cache (CHI defaults matching Delegato paper Table 3)
    parser.add_argument("--num-dirs", type=int, default=8,
                        help="Number of memory controllers (default: 8)")
    parser.add_argument("--num-l2caches", type=int, default=1)
    parser.add_argument("--num-l3caches", type=int, default=32,
                        help="Number of L3/HNF slices (default: 32)")
    parser.add_argument("--l1d_size", type=str, default="64KiB")
    parser.add_argument("--l1i_size", type=str, default="64KiB")
    parser.add_argument("--l2_size", type=str, default="1MiB")
    parser.add_argument("--l3_size", type=str, default="1MiB")
    parser.add_argument("--l1d_assoc", type=int, default=4)
    parser.add_argument("--l1i_assoc", type=int, default=4)
    parser.add_argument("--l2_assoc", type=int, default=8)
    parser.add_argument("--l3_assoc", type=int, default=16)
    parser.add_argument("--cacheline_size", type=int, default=64)

    # Ruby/Network
    Ruby.define_options(parser)

    # AMO policy
    parser.add_argument("--amo-policy", type=str, default="delegato",
                        choices=["delegato", "dynamo", "unique-near", "all-far"],
                        help="AMO placement policy: delegato (default), dynamo, "
                             "unique-near (baseline), all-far")

    args = parser.parse_args()

    # Argument validation
    if args.bare_metal:
        if args.kernel:
            parser.error("--bare-metal and --kernel are mutually exclusive")
        if args.initrd:
            parser.error("--bare-metal and --initrd are mutually exclusive")
        if args.fast_forward:
            parser.error("--fast-forward requires m5ops; not available in bare-metal mode")
        if args.save_checkpoint:
            parser.error("--save-checkpoint requires m5ops; not available in bare-metal mode")
        if args.restore:
            parser.error("--restore is not supported in bare-metal mode")
    elif not args.kernel:
        parser.error("--kernel is required (unless --bare-metal is specified)")

    # Force topology and CHI config for Delegato
    # Single-die fast-test config for ≤4 cores; dual-chiplet 4×12 otherwise
    if args.num_cpus <= 4:
        noc_name = "delegato_single_2x4.py"
        # Override cache/memory defaults for the smaller topology
        args.num_l3caches = 4
        args.num_dirs = 2
        args.mem_channels = 2
        print("Using single-die 2×4 mesh (fast-test config)")
    else:
        noc_name = "delegato_4x12.py"

    noc_config = os.path.join(
        os.path.dirname(__file__), "..", "noc_config", noc_name
    )
    args.topology = "CustomMesh"
    args.chi_config = noc_config
    args.network = "garnet"
    args.ruby_clock = "2GHz"

    root = Root(full_system=True)
    root.system = create(args)

    if args.restore:
        # Restore from checkpoint, then switch CPUs immediately
        m5.instantiate(args.restore)
        system = root.system
        if hasattr(system, "switch_cpus"):
            n = len(system.switch_cpus)
            switch_cpu_list = [
                (system.cpu_cluster[0].cpus[i], system.switch_cpus[i])
                for i in range(n)
            ]
            print(f"Restored, switching {n} CPUs to {args.cpu}")
            m5.switchCpus(system, switch_cpu_list)
            print("CPU switch complete, resuming ROI")
    else:
        m5.instantiate()

    run(args, root)


if __name__ == "__m5_main__":
    main()
