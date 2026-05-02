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
  Normal:       gem5.opt delegato_fs.py --kernel <Image> --initrd <cpio.gz> --cpu <timing|minor|hpi|o3|kvm>
                Boot and run on the same CPU from start to finish.
  KVM ROI ckpt: gem5.opt delegato_fs.py ... --cpu <timing|minor|o3> --save-kvm-roi-checkpoint
                Boot with ArmV8KvmCPU, save a cold-start ROI checkpoint at ROI,
                then exit.
  Restore:      gem5.opt delegato_fs.py ... --cpu <timing|minor|o3> --restore <cpt_dir>
                Restore directly from a KVM ROI checkpoint using the same
                target CPU it was generated for.
  Bare-metal:   gem5.opt delegato_fs.py --bare-metal <elf> --cpu timing
                Boot bare-metal ELF directly (no Linux kernel).  The ELF
                must be linked at 0x80080000 (VExpress physical RAM + 512K).
                Uses boot.arm64 bootloader for DTB passing and spin-table
                secondary CPU boot.
"""

import argparse
import json
import math
import os
import sys

import m5
from m5.SimObject import SimObject
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
    "timing": TimingSimpleCPU,
    "minor": MinorCPU,
    "hpi": HPI.HPI,
    "o3": DelegatoO3CPU,
}
kvm_cpu_class = ObjectList.cpu_list.get("ArmV8KvmCPU") if devices.have_kvm else None
if kvm_cpu_class is not None:
    cpu_types["kvm"] = kvm_cpu_class

CHECKPOINT_METADATA_NAME = "checkpoint_metadata.json"
LATEST_CHECKPOINT_NAME = "latest_checkpoint.txt"
KVM_ROI_CHECKPOINT_KIND = "kvm_roi_post_switch"
KVM_SIM_QUANTUM = "1ms"
KVM_AFFINITY_CLUSTER_SIZE = 16


def _to_ticks(value):
    """Convert a latency string to ticks."""

    return m5.ticks.fromSeconds(m5.util.convert.anyToLatency(value))


def _using_pdes(root):
    """Determine whether the configuration uses multiple event queues."""

    for obj in root.descendants():
        if (
            not m5.proxy.isproxy(obj.eventq_index)
            and obj.eventq_index != root.eventq_index
        ):
            return True

    return False


def _resolve_restore_dir(path):
    """Allow restoring from either a checkpoint dir or its outdir."""

    if path is None:
        return None

    if os.path.isfile(os.path.join(path, "m5.cpt")):
        return path

    latest_path = os.path.join(path, LATEST_CHECKPOINT_NAME)
    if os.path.isfile(latest_path):
        with open(latest_path, "r", encoding="utf-8") as fh:
            resolved = fh.read().strip()
        if resolved:
            return resolved

    return path


def _load_checkpoint_metadata(path):
    """Load checkpoint sidecar metadata if present."""

    if path is None:
        return None

    metadata_path = os.path.join(path, CHECKPOINT_METADATA_NAME)
    if not os.path.isfile(metadata_path):
        return None

    with open(metadata_path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _is_post_switch_checkpoint(metadata):
    return bool(metadata) and metadata.get("kind") == KVM_ROI_CHECKPOINT_KIND


def _build_checkpoint_metadata(args, kind):
    metadata = {
        "kind": kind,
        "target_cpu": args.cpu,
        "num_cpus": args.num_cpus,
        "cacheline_size": args.cacheline_size,
    }

    if kind == KVM_ROI_CHECKPOINT_KIND:
        metadata.update({
            "boot_cpu": "kvm",
            "policy_agnostic": True,
            "ruby_cold_start": True,
        })

    return metadata


def _write_checkpoint_metadata(cpt_dir, metadata):
    metadata_path = os.path.join(cpt_dir, CHECKPOINT_METADATA_NAME)
    with open(metadata_path, "w", encoding="utf-8") as fh:
        json.dump(metadata, fh, indent=2, sort_keys=True)
        fh.write("\n")

    latest_path = os.path.join(os.path.dirname(cpt_dir), LATEST_CHECKPOINT_NAME)
    with open(latest_path, "w", encoding="utf-8") as fh:
        fh.write(cpt_dir)
        fh.write("\n")


def _retarget_switch_cpu_checkpoint(cpt_dir, num_cpus):
    checkpoint_path = os.path.join(cpt_dir, "m5.cpt")
    with open(checkpoint_path, "r", encoding="utf-8") as fh:
        lines = fh.readlines()

    keep_cpu_cluster_prefixes = (
        ".data_sequencer",
        ".inst_sequencer",
        ".interrupts",
        ".l1d",
        ".l1i",
        ".l2",
    )

    rewritten = []
    keep_section = True
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1]
            keep_section = True
            for idx in range(num_cpus):
                switch_prefix = f"system.switch_cpus{idx}"
                cpu_prefix = f"system.cpu_cluster.cpus{idx}"
                if section == switch_prefix or section.startswith(switch_prefix + "."):
                    suffix = section[len(switch_prefix):]
                    line = f"[{cpu_prefix}{suffix}]\n"
                    break
                if section == cpu_prefix:
                    keep_section = False
                    break
                if section.startswith(cpu_prefix + "."):
                    suffix = section[len(cpu_prefix):]
                    keep_section = suffix.startswith(keep_cpu_cluster_prefixes)
                    break

        if keep_section:
            for idx in range(num_cpus):
                line = line.replace(
                    f"system.switch_cpus{idx}",
                    f"system.cpu_cluster.cpus{idx}",
                )
            rewritten.append(line)

    with open(checkpoint_path, "w", encoding="utf-8") as fh:
        fh.writelines(rewritten)


def _save_checkpoint(args, kind):
    cpt_dir = os.path.join(m5.options.outdir, f"cpt.{m5.curTick()}")
    m5.checkpoint(cpt_dir)
    if kind == KVM_ROI_CHECKPOINT_KIND:
        _retarget_switch_cpu_checkpoint(cpt_dir, args.num_cpus)
    _write_checkpoint_metadata(cpt_dir, _build_checkpoint_metadata(args, kind))
    return cpt_dir


def _get_switch_cpu_list(system):
    boot_cpus = _get_boot_cpus(system)
    n = len(system.switch_cpus)
    return [
        (boot_cpus[i], system.switch_cpus[i])
        for i in range(n)
    ]


def _get_boot_cpus(system):
    return [
        cpu
        for cluster in system.cpu_cluster
        for cpu in cluster.cpus
    ]


def _set_cpu_heartbeat(system, heartbeat_insts):
    for cpu in _get_boot_cpus(system):
        cpu.heartbeat_insts = heartbeat_insts

    if hasattr(system, "switch_cpus"):
        for cpu in system.switch_cpus:
            cpu.heartbeat_insts = heartbeat_insts


def _enable_kvm(system, use_pdes=True):
    if not devices.have_kvm or kvm_cpu_class is None:
        m5.fatal("ArmV8KvmCPU is not available in this gem5 build")

    system.kvm_vm = KvmVM()
    system.release = ArmDefaultRelease.for_kvm()

    boot_cpus = _get_boot_cpus(system)

    # Disable perf_event usage when the host restricts it
    # (perf_event_paranoid > 1).  KVM still works but without
    # hardware perf counters.
    try:
        with open("/proc/sys/kernel/perf_event_paranoid") as f:
            paranoid = int(f.read().strip())
    except (OSError, ValueError):
        paranoid = 0
    if paranoid > 1:
        for cpu in boot_cpus:
            cpu.usePerf = False

    # PDES: assign each boot CPU (and its KVM-side descendants) to its
    # own event queue so multiple vCPUs can run in parallel.  Devices
    # like the GIC remain on queue 0, and KVM in-kernel interrupt
    # delivery handles cross-CPU IPIs without going through gem5
    # event scheduling.
    if use_pdes and len(boot_cpus) > 1:
        for idx, cpu in enumerate(boot_cpus):
            for obj in cpu.descendants():
                obj.eventq_index = 0
            cpu.eventq_index = idx + 1


# ─── System creation ─────────────────────────────────────────────────────

def create(args, restore_metadata=None):
    """Create and configure the system."""

    use_folded_kvm_affinity = False

    # Use VExpress_GEM5_Foundation (GICv3) for any mode that boots with
    # KVM or restores a KVM-generated checkpoint. GICv3-only hosts cannot
    # create a KVM GICv2 kernel device (VExpress_GEM5_V1), but
    # MuxingKvmGicV3 works natively and restore must recreate the same
    # platform/GIC object graph that was serialized into the checkpoint.
    use_kvm_boot = (
        args.cpu == "kvm"
        or args.save_kvm_roi_checkpoint
        or args.kvm_fast_forward
        or args.restore
    )
    if use_kvm_boot and args.num_cpus > KVM_AFFINITY_CLUSTER_SIZE:
        use_folded_kvm_affinity = True
    platform = VExpress_GEM5_Foundation() if use_kvm_boot else None
    if platform is not None:
        # KVM requires it_lines to be a multiple of 32; the Gicv3
        # default (1020) is not.  Use 512 to match VExpress_GEM5_V1.
        platform.gic.it_lines = 512
        # Foundation's Pl111 CLCD has a DMA port that the Ruby/CHI
        # connect() flow cannot wire up.  Replace it with a no-op fake
        # since we don't need a display controller.
        platform.clcd = AmbaFake(pio_addr=0x1C1F0000, ignore_access=True)
        # Keep the default kernel VGIC path for KVM boots. The guest uses the
        # GICv3 system-register CPU interface for SGIs during SMP bring-up,
        # which requires KVM's in-kernel VGIC rather than gem5's userspace GIC.

    # Determine boot CPU vs target CPU
    if args.save_kvm_roi_checkpoint or args.kvm_fast_forward:
        boot_cpu_class = kvm_cpu_class
        target_cpu_class = cpu_types[args.cpu]
    elif args.restore:
        boot_cpu_class = cpu_types[args.cpu]
        target_cpu_class = None
    else:
        boot_cpu_class = cpu_types[args.cpu]
        target_cpu_class = None

    mem_mode = boot_cpu_class.memory_mode()

    # Bare-metal vs Linux workload
    if args.bare_metal:
        system = devices.ArmRubySystem(
            args.mem_size,
            mem_mode=mem_mode,
            platform=platform,
            kvm_affinity_fold_16=use_folded_kvm_affinity,
            workload=ArmFsWorkload(object_file=args.bare_metal),
        )
    else:
        system = devices.ArmRubySystem(
            args.mem_size,
            mem_mode=mem_mode,
            platform=platform,
            kvm_affinity_fold_16=use_folded_kvm_affinity,
            workload=ArmFsLinux(object_file=args.kernel),
        )

    if args.restore:
        system.release = ArmDefaultRelease.for_kvm()

    # CPU cluster (boot CPUs)
    boot_cluster_sizes = [args.num_cpus]
    if use_kvm_boot and args.num_cpus > KVM_AFFINITY_CLUSTER_SIZE:
        boot_cluster_sizes = []
        remaining_cpus = args.num_cpus
        while remaining_cpus > 0:
            cluster_cpus = min(KVM_AFFINITY_CLUSTER_SIZE, remaining_cpus)
            boot_cluster_sizes.append(cluster_cpus)
            remaining_cpus -= cluster_cpus

    system.cpu_cluster = [
        devices.ArmCpuCluster(
            system,
            cluster_cpus,
            args.cpu_freq,
            "1.0V",
            boot_cpu_class,
            None,  # L1I handled by Ruby/CHI
            None,  # L1D handled by Ruby/CHI
            None,  # L2 handled by Ruby/CHI
        )
        for cluster_cpus in boot_cluster_sizes
    ]
    # Create switched-out target CPUs for KVM ROI save
    # Follow gem5 standard pattern (configs/common/Simulation.py):
    #   - Do NOT call createInterruptController() — interrupts transfer
    #     via takeOverFrom() during m5.switchCpus()
    if target_cpu_class and target_cpu_class is not boot_cpu_class:
        switch_cpus = []
        for boot_cpu in _get_boot_cpus(system):
            cpu = target_cpu_class(
                switched_out=True,
                cpu_id=boot_cpu.cpu_id,
                socket_id=boot_cpu.socket_id,
                clk_domain=boot_cpu.clk_domain,
            )
            cpu.createThreads()
            switch_cpus.append(cpu)
        system.switch_cpus = switch_cpus
    _set_cpu_heartbeat(system, args.heartbeat_insts)

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
    if restore_metadata and restore_metadata.get("ruby_cold_start"):
        system.ruby.skip_warmup_restore = True
    system._ruby_cpu_port_targets = cpus

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
        enable_switch_notice = args.save_kvm_roi_checkpoint or args.kvm_fast_forward or args.restore
        if args.save_kvm_roi_checkpoint or args.kvm_fast_forward or args.restore:
            kernel_cmd.append("gem5_m5ops_mmio=1")
            kernel_cmd.append("iomem=relaxed")
        if enable_switch_notice:
            kernel_cmd.append("gem5_cpu_switch_notice=1")

        system.workload.command_line = " ".join(kernel_cmd)

    return system


# ─── Simulation loop ─────────────────────────────────────────────────────

def run(args, root, switched=False):
    has_switch_cpus = hasattr(root.system, "switch_cpus")

    while True:
        event = m5.simulate()
        exit_msg = event.getCause()

        if exit_msg == "checkpoint":
            if not (args.save_kvm_roi_checkpoint or args.kvm_fast_forward):
                m5.fatal("Unexpected checkpoint event outside KVM ROI checkpoint mode")

            if not has_switch_cpus or switched:
                m5.fatal("KVM ROI checkpoint flow requires a pending CPU switch")

            if args.save_kvm_roi_checkpoint:
                print(f"Switching CPUs at tick {m5.curTick()}")
                m5.switchCpus(root.system, _get_switch_cpu_list(root.system))
                switched = True
                print(f"CPU switch complete, saving checkpoint @ tick {m5.curTick()}")
                cpt_dir = _save_checkpoint(args, KVM_ROI_CHECKPOINT_KIND)
                print(f"KVM ROI checkpoint saved: {cpt_dir}")
                sys.exit(0)
            else:  # args.kvm_fast_forward
                print(f"Switching CPUs at tick {m5.curTick()}")
                m5.switchCpus(root.system, _get_switch_cpu_list(root.system))
                switched = True
                print(f"CPU switch complete, resuming simulation @ tick {m5.curTick()}")
                continue  # loop back to m5.simulate()

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
                             "Mutually exclusive with --kernel/--initrd.")
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
    parser.add_argument("-n", "--num-cpus", type=int, default=16,
                        help="Number of CPUs: 4, 16, or 32 (default: 16)")
    parser.add_argument("--heartbeat-insts", type=int, default=0,
                        help="Print a per-core heartbeat every N committed "
                             "instructions on timing/o3 CPUs; 0 disables")

    parser.add_argument("--save-kvm-roi-checkpoint", action="store_true",
                        help="Boot with ArmV8KvmCPU, switch to --cpu at ROI "
                             "start, immediately save a cold-start ROI checkpoint, "
                             "and exit")
    parser.add_argument("--kvm-fast-forward", action="store_true",
                        help="Boot with ArmV8KvmCPU, switch to --cpu at ROI, "
                             "continue simulation without saving checkpoint")
    parser.add_argument("--restore", type=str, default=None,
                         help="Restore directly from a KVM ROI checkpoint "
                              "directory (or outdir with latest_checkpoint.txt)")

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
    args.restore = _resolve_restore_dir(args.restore)
    restore_metadata = _load_checkpoint_metadata(args.restore)

    # Argument validation
    if args.bare_metal:
        if args.kernel:
            parser.error("--bare-metal and --kernel are mutually exclusive")
        if args.initrd:
            parser.error("--bare-metal and --initrd are mutually exclusive")
        if args.save_kvm_roi_checkpoint:
            parser.error("--save-kvm-roi-checkpoint is not available in bare-metal mode")
        if args.restore:
            parser.error("--restore is not supported in bare-metal mode")
    elif not args.kernel:
        parser.error("--kernel is required (unless --bare-metal is specified)")

    if args.save_kvm_roi_checkpoint and args.cpu not in ("timing", "minor", "o3"):
        parser.error("--save-kvm-roi-checkpoint requires --cpu timing, minor, or o3")
    if args.save_kvm_roi_checkpoint and not devices.have_kvm:
        parser.error("ArmV8KvmCPU is not available in this gem5 build")

    if args.kvm_fast_forward:
        if args.cpu not in ("timing", "minor", "o3"):
            parser.error("--kvm-fast-forward requires --cpu timing, minor, or o3")
        if args.save_kvm_roi_checkpoint:
            parser.error("--kvm-fast-forward and --save-kvm-roi-checkpoint are mutually exclusive")
        if args.restore:
            parser.error("--kvm-fast-forward and --restore are mutually exclusive")
        if args.bare_metal:
            parser.error("--kvm-fast-forward is not available in bare-metal mode")
        if not devices.have_kvm:
            parser.error("ArmV8KvmCPU is not available in this gem5 build")

    if args.restore:
        if not os.path.isfile(os.path.join(args.restore, "m5.cpt")):
            parser.error(f"Invalid checkpoint directory: {args.restore}")

        saved_num_cpus = None if restore_metadata is None else restore_metadata.get("num_cpus")
        if saved_num_cpus is not None and args.num_cpus != saved_num_cpus:
            parser.error(
                f"--restore checkpoint expects --num-cpus {saved_num_cpus}, got {args.num_cpus}"
            )

        if args.cpu not in ("timing", "minor", "o3"):
            parser.error("--restore requires --cpu timing, minor, or o3")

        if not _is_post_switch_checkpoint(restore_metadata):
            parser.error("--restore only supports KVM ROI checkpoints")

        saved_cpu = restore_metadata.get("target_cpu")
        if not saved_cpu:
            parser.error("--restore checkpoint metadata is missing target_cpu")
        if saved_cpu not in ("timing", "minor", "o3"):
            parser.error(
                f"--restore checkpoint has unsupported target_cpu {saved_cpu}"
            )
        if args.cpu != saved_cpu:
            parser.error(
                f"--restore checkpoint expects --cpu {saved_cpu}, got {args.cpu}"
            )

    # Force topology and CHI config for Delegato
    # Explicit dispatch: 4 => 2x4, 16 => 4x8, 32 => 4x12
    if args.num_cpus not in (4, 16, 32):
        parser.error("--num-cpus only supports 4, 16, or 32")
    if args.heartbeat_insts < 0:
        parser.error("--heartbeat-insts must be a non-negative integer")
    
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
    else:  # args.num_cpus == 32
        noc_name = "delegato_4x12.py"
        args.num_l3caches = 32
        args.num_dirs = 8
        args.mem_channels = 8
        print("Using dual-chiplet 4x12 mesh (32-core full config)")

    noc_config = os.path.join(
        os.path.dirname(__file__), "..", "noc_config", noc_name
    )
    args.topology = "CustomMesh"
    args.chi_config = noc_config
    args.network = "garnet"
    args.ruby_clock = "2GHz"

    root = Root(full_system=True)
    root.system = create(args, restore_metadata)
    if args.cpu == "kvm" or args.save_kvm_roi_checkpoint or args.kvm_fast_forward:
        _enable_kvm(root.system)
        if _using_pdes(root):
            root.sim_quantum = int(1e9)  # 1ms at default 1THz tick rate

    switched = False
    if args.restore:
        m5.instantiate(args.restore)
    else:
        m5.instantiate()

    run(args, root, switched=switched)


if __name__ == "__m5_main__":
    main()
