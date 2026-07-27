# Copyright (c) 2026
# All rights reserved.

"""AMO policy resolver for Delegato / DynAAN experiments.

Unified policy naming.  Every runnable policy name carries an explicit
D2D cross-die link-latency suffix ``-lat<N>`` (cycles); there are no bare
aliases.  The latency axis is orthogonal to every other knob, so it is
always appended last:

    <stem>-lat<N>

Static baselines (no AAN knobs):
    all-near-lat<N>   all-central-lat<N>   dynamo-lat<N>   delegato-lat<N>

Motivation probe (naive boundary interception on the All-Central base;
the only axis change vs all-central is AAN bypass -> near, i.e. admit-all
boundary execution with a 16KiB array and no BAT filter):
    all-central-aan-lat<N>

DynAAN design point (BAT 256 entries, AAN cache 4KiB, BAT lifetime 5000):
    dynaan-lat<N>

DynAAN ablation stems (hold two knobs over-provisioned, sweep the third,
all at c16k/bat2048 unless swept):
    BAT sweep       dynaan-bat{0,32,128,256,512,2048}-c16k-lat<N>
    cache sweep     dynaan-bat2048-c{1,2,4,8,16}k-lat<N>
    lifetime sweep  dynaan-bat2048-c16k[-lt{0,1k,10k,50k,1m}]-lat<N>
                    (the lt5000 midpoint is dynaan-bat2048-c16k itself)

``bat0`` is special: it maps to aan_policy=near (admit-all, the BAT filter
is bypassed) rather than filter -- the "No Filter" end of the BAT sweep.
D2D link *width* is fixed at 64B/flit and is no longer an ablation axis.
"""

from dataclasses import dataclass
import re


L1D_POLICY_CODES = {
    "near": 0,
    "unique-near": 1,
    "dynamo": 2,
}

AAN_POLICY_CODES = {
    "bypass": 0,
    "near": 1,
    "filter": 2,
}

HNF_POLICY_CODES = {
    "central": 0,
    "migrate": 1,
    "pc": 2,
    "po": 3,
    "ca": 4,
    "delegato": 5,
    "pa": 6,
}

# Internal protocol axis definitions (l1d, aan, hnf).  These are the
# building blocks every user-facing policy resolves to; they are not
# selectable on their own (they carry no D2D-latency suffix).  The static
# directory sub-policies (pc/po/ca/all-migrate) and pinned-AAN variants
# remain reachable through the axis overrides (--l1d/--aan/--hnf-amo-policy).
PROTOCOL_AXES = {
    "all-near": ("near", "bypass", "central"),
    "all-central": ("unique-near", "bypass", "central"),
    "dynamo": ("dynamo", "bypass", "central"),
    "delegato": ("unique-near", "bypass", "delegato"),
    # Motivation probe: All-Central plus naive (admit-all) boundary
    # interception; differs from all-central only on the AAN axis.
    "all-central-aan": ("unique-near", "near", "central"),
    "dynaan-nofilter": ("dynamo", "near", "central"),
    "dynaan-filter": ("dynamo", "filter", "central"),
}

# Canonical D2D link latencies (cycles) used to enumerate valid policy
# names.  The performance/ablation experiments pin latency at 100; the
# D2D-latency sweep uses the full set.
LATENCIES = (1, 50, 100, 150, 200)

# DynAAN design-point AAN knobs.
_DYNAAN_DP = {
    "aan_bat_entries": 256,
    "aan_cache_kib": 4,
    "aan_bat_lifetime_cycles": 5000,
}

# Policy "shapes": stem -> (protocol-axis key, AAN knob overrides).  The
# D2D link latency is supplied separately via the mandatory -lat<N> suffix.
SHAPE_MAP = {
    # Static baselines (no AAN knobs).
    "all-near": ("all-near", {}),
    "all-central": ("all-central", {}),
    "dynamo": ("dynamo", {}),
    "delegato": ("delegato", {}),
    # Motivation probe: naive boundary interception on the All-Central
    # base (admit-all, 16KiB array to keep capacity out of the picture;
    # the BAT is bypassed in aan=near mode so its size is irrelevant).
    "all-central-aan": ("all-central-aan", {"aan_cache_kib": 16}),
    # DynAAN design point: filter + BAT 256 + AAN cache 4KiB + lifetime 5000.
    "dynaan": ("dynaan-filter", dict(_DYNAAN_DP)),
    # A. BAT capacity sweep (c16k / lt5000 fixed so only the table moves).
    # bat0 = No Filter (admit-all) end.
    "dynaan-bat0-c16k":    ("dynaan-nofilter", {"aan_cache_kib": 16}),
    "dynaan-bat32-c16k":   ("dynaan-filter", {"aan_bat_entries": 32, "aan_cache_kib": 16, "aan_bat_lifetime_cycles": 5000}),
    "dynaan-bat128-c16k":  ("dynaan-filter", {"aan_bat_entries": 128, "aan_cache_kib": 16, "aan_bat_lifetime_cycles": 5000}),
    "dynaan-bat256-c16k":  ("dynaan-filter", {"aan_bat_entries": 256, "aan_cache_kib": 16, "aan_bat_lifetime_cycles": 5000}),
    "dynaan-bat512-c16k":  ("dynaan-filter", {"aan_bat_entries": 512, "aan_cache_kib": 16, "aan_bat_lifetime_cycles": 5000}),
    # Over-provisioned max, shared by the BAT / cache / lifetime sweeps
    # (== bat2048, c16k, lt5000).
    "dynaan-bat2048-c16k": ("dynaan-filter", {"aan_bat_entries": 2048, "aan_cache_kib": 16, "aan_bat_lifetime_cycles": 5000}),
    # B. AAN cache capacity sweep (bat2048 / lt5000 fixed).
    "dynaan-bat2048-c1k":  ("dynaan-filter", {"aan_bat_entries": 2048, "aan_cache_kib": 1, "aan_bat_lifetime_cycles": 5000}),
    "dynaan-bat2048-c2k":  ("dynaan-filter", {"aan_bat_entries": 2048, "aan_cache_kib": 2, "aan_bat_lifetime_cycles": 5000}),
    "dynaan-bat2048-c4k":  ("dynaan-filter", {"aan_bat_entries": 2048, "aan_cache_kib": 4, "aan_bat_lifetime_cycles": 5000}),
    "dynaan-bat2048-c8k":  ("dynaan-filter", {"aan_bat_entries": 2048, "aan_cache_kib": 8, "aan_bat_lifetime_cycles": 5000}),
    # C. BAT entry-lifetime sweep (leaky-bucket window, cycles) on the
    # oversized bat2048/c16k table so capacity eviction cannot mask the
    # lifetime effect.  lt0 = capacity-only end (lifetimes disabled); the
    # lt5000 midpoint is dynaan-bat2048-c16k above.  Expect both extremes
    # to hurt: ultra-short kills SCQ re-admission after recalls, ultra-long
    # reverts to capacity-only and readmits KME churn.
    "dynaan-bat2048-c16k-lt0":   ("dynaan-filter", {"aan_bat_entries": 2048, "aan_cache_kib": 16, "aan_bat_lifetime_cycles": 0}),
    "dynaan-bat2048-c16k-lt1k":  ("dynaan-filter", {"aan_bat_entries": 2048, "aan_cache_kib": 16, "aan_bat_lifetime_cycles": 1000}),
    "dynaan-bat2048-c16k-lt10k": ("dynaan-filter", {"aan_bat_entries": 2048, "aan_cache_kib": 16, "aan_bat_lifetime_cycles": 10000}),
    "dynaan-bat2048-c16k-lt50k": ("dynaan-filter", {"aan_bat_entries": 2048, "aan_cache_kib": 16, "aan_bat_lifetime_cycles": 50000}),
    "dynaan-bat2048-c16k-lt1m":  ("dynaan-filter", {"aan_bat_entries": 2048, "aan_cache_kib": 16, "aan_bat_lifetime_cycles": 1000000}),
}


_LAT_RE = re.compile(r"^(?P<stem>.+)-lat(?P<lat>\d+)$")


@dataclass(frozen=True)
class AMOPolicy:
    top_policy: str
    l1d_policy: str
    aan_policy: str
    hnf_policy: str

    @property
    def l1d_policy_code(self):
        return L1D_POLICY_CODES[self.l1d_policy]

    @property
    def aan_policy_code(self):
        return AAN_POLICY_CODES[self.aan_policy]

    @property
    def hnf_policy_code(self):
        return HNF_POLICY_CODES[self.hnf_policy]

    @property
    def delegato_rt_enabled(self):
        return self.hnf_policy == "delegato"


def _validate(name, value, valid):
    if value not in valid:
        choices = ", ".join(valid)
        raise ValueError(f"unknown {name} '{value}'. Choose: {choices}")


def _split_latency(name):
    """Split a policy name into (stem, d2d_latency_cycles)."""
    match = _LAT_RE.match(name or "")
    if not match:
        raise ValueError(
            f"policy '{name}' has no -lat<N> suffix; every policy name must "
            f"end in -lat<cycles>, e.g. dynaan-lat100 or all-near-lat50"
        )
    return match.group("stem"), int(match.group("lat"))


def _resolve_shape(name):
    """Resolve a full policy name to (protocol-axis key, knob overrides)."""
    stem, latency = _split_latency(name)
    if stem not in SHAPE_MAP:
        raise ValueError(
            f"unknown policy stem '{stem}' in '{name}'. Known stems: "
            + ", ".join(SHAPE_MAP)
        )
    base, knobs = SHAPE_MAP[stem]
    overrides = dict(knobs)
    overrides["d2d_link_latency"] = latency
    return base, overrides


def base_policy_name(top_policy):
    """Map a user-facing policy name to its protocol-axis key."""
    return _resolve_shape(top_policy)[0]


def ablation_overrides(top_policy):
    """Knob overrides (AAN sizing + D2D latency) for a policy name."""
    return _resolve_shape(top_policy)[1]


def all_policy_names():
    """Every runnable policy name: {stem}-lat{N} over the canonical grid."""
    return [
        f"{stem}-lat{latency}"
        for stem in SHAPE_MAP
        for latency in LATENCIES
    ]


def resolve_amo_policy(
    top_policy,
    l1d_policy=None,
    aan_policy=None,
    hnf_policy=None,
):
    base = base_policy_name(top_policy)
    _validate("top AMO policy", base, PROTOCOL_AXES)
    resolved_l1d, resolved_aan, resolved_hnf = PROTOCOL_AXES[base]

    if l1d_policy is not None:
        _validate("L1D AMO policy", l1d_policy, L1D_POLICY_CODES)
        resolved_l1d = l1d_policy
    if aan_policy is not None:
        _validate("AAN AMO policy", aan_policy, AAN_POLICY_CODES)
        resolved_aan = aan_policy
    if hnf_policy is not None:
        _validate("HNF AMO policy", hnf_policy, HNF_POLICY_CODES)
        resolved_hnf = hnf_policy

    return AMOPolicy(top_policy, resolved_l1d, resolved_aan, resolved_hnf)


def apply_ablation_overrides(args):
    """Fold the policy's knob overrides into args (explicit CLI wins)."""
    for dest, value in ablation_overrides(args.amo_policy).items():
        if getattr(args, dest, None) is None:
            setattr(args, dest, value)


def add_amo_policy_args(parser):
    parser.add_argument(
        "--amo-policy",
        type=str,
        default="dynaan-lat100",
        choices=all_policy_names(),
        help="Top-level AMO policy (<stem>-lat<cycles>; see amo_policy.py)",
    )
    parser.add_argument(
        "--l1d-amo-policy",
        type=str,
        default=None,
        choices=list(L1D_POLICY_CODES),
        help="Override L1D AMO policy",
    )
    parser.add_argument(
        "--aan-amo-policy",
        type=str,
        default=None,
        choices=list(AAN_POLICY_CODES),
        help="Override AAN AMO policy",
    )
    parser.add_argument(
        "--hnf-amo-policy",
        type=str,
        default=None,
        choices=list(HNF_POLICY_CODES),
        help="Override HNF AMO policy",
    )
    # Ablation knobs.  None = platform default (BAT 256 entries, AAN cache
    # 4KiB, BAT lifetime 5000 cycles, D2D 64B/flit, D2D latency from the
    # noc_config).  Policy names pre-fill these through
    # apply_ablation_overrides; explicit CLI values take precedence.
    parser.add_argument(
        "--aan-bat-entries",
        type=int,
        default=None,
        help="AAN boundary admission table entries (default 256)",
    )
    parser.add_argument(
        "--aan-bat-lifetime-cycles",
        type=int,
        default=None,
        help="BAT entry lifetime window in cycles (leaky bucket: expired "
        "window costs one 5-bit reuse credit, spent entry dies; admits "
        "recharge). Default 5000; 0 = capacity-only eviction",
    )
    parser.add_argument(
        "--aan-cache-kib",
        type=int,
        default=None,
        help="AAN data array capacity in KiB (default 4)",
    )
    parser.add_argument(
        "--d2d-link-width",
        type=int,
        default=None,
        help="Cross-die link width in bytes/flit (fixed 64)",
    )
    parser.add_argument(
        "--d2d-link-latency",
        type=int,
        default=None,
        help="Cross-die link latency in cycles (set by the -lat<N> policy "
        "suffix; falls back to the noc_config default when unset)",
    )
