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

Usage:
    gem5.opt delegato_fs.py --kernel <Image> --initrd <cpio.gz> [options]
"""

import argparse
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

cpu_types = {
    "atomic": NonCachingSimpleCPU,
    "timing": TimingSimpleCPU,
    "minor": MinorCPU,
    "hpi": HPI.HPI,
    "o3": O3_ARM_v7a.O3_ARM_v7a_3,
}


def create(args):
    """Create and configure the system."""

    cpu_class = cpu_types[args.cpu]
    mem_mode = cpu_class.memory_mode()

    system = devices.ArmRubySystem(
        args.mem_size,
        mem_mode=mem_mode,
        workload=ArmFsLinux(object_file=args.kernel),
    )

    # CPU cluster
    system.cpu_cluster = [
        devices.ArmCpuCluster(
            system,
            args.num_cpus,
            args.cpu_freq,
            "1.0V",
            cpu_class,
            None,  # L1I handled by Ruby/CHI
            None,  # L1D handled by Ruby/CHI
            None,  # L2 handled by Ruby/CHI
        )
    ]

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

    system.ruby.clk_domain = SrcClockDomain(
        clock=args.ruby_clock, voltage_domain=system.voltage_domain
    )

    system.connect()
    system.realview.setupBootLoader(
        system, SysPaths.binary,
        boot_loader=[SysPaths.binary("boot.arm64")]
    )

    if args.dtb:
        system.workload.dtb_filename = args.dtb
    else:
        system.workload.dtb_filename = os.path.join(
            m5.options.outdir, "system.dtb"
        )
        system.generateDtb(system.workload.dtb_filename)

    # initrd support
    if args.initrd:
        system.workload.initrd_filename = args.initrd

    # Kernel command line
    kernel_cmd = [
        "console=ttyAMA0",
        "lpj=19988480",
        "norandmaps",
        f"mem={args.mem_size}",
    ]
    if args.disk_image:
        kernel_cmd.append(f"root={args.root_device}")
        kernel_cmd.append("rw")
    if args.initrd:
        kernel_cmd.append("rdinit=/init")

    system.workload.command_line = " ".join(kernel_cmd)

    return system


def run():
    while True:
        event = m5.simulate()
        exit_msg = event.getCause()
        if exit_msg == "checkpoint":
            print(f"Dropping checkpoint at tick {m5.curTick()}")
            cpt_dir = os.path.join(m5.options.outdir, f"cpt.{m5.curTick()}")
            m5.checkpoint(cpt_dir)
            print("Checkpoint done.")
        else:
            print(f"{exit_msg} @ {m5.curTick()}")
            break
    sys.exit(event.getCode())


def main():
    parser = argparse.ArgumentParser(
        description="Delegato dual-chiplet FS simulation"
    )

    # System
    parser.add_argument("--kernel", type=str, required=True,
                        help="Path to kernel Image")
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
    args = parser.parse_args()

    # Force topology and CHI config for Delegato
    noc_config = os.path.join(
        os.path.dirname(__file__), "..", "noc_config", "delegato_4x12.py"
    )
    args.topology = "CustomMesh"
    args.chi_config = noc_config
    args.network = "garnet"
    args.ruby_clock = "2GHz"

    root = Root(full_system=True)
    root.system = create(args)

    m5.instantiate()
    run()


if __name__ == "__m5_main__":
    main()
