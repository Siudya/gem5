# Copyright (c) 2026
# All rights reserved.

"""AMO policy resolver for Delegato experiments."""

from dataclasses import dataclass


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

# Main policies. The static directory sub-policies (pc/po/ca/all-migrate)
# and the pinned-AAN variants remain reachable through the axis overrides
# (--l1d/--aan/--hnf-amo-policy) but are no longer top-level names.
TOP_POLICY_MAP = {
    "all-near": ("near", "bypass", "central"),
    "all-central": ("unique-near", "bypass", "central"),
    "dynamo": ("dynamo", "bypass", "central"),
    "delegato": ("unique-near", "bypass", "delegato"),
    "dynaan-nofilter": ("dynamo", "near", "central"),
    "dynaan-filter": ("dynamo", "filter", "central"),
}

# Ablation policies: parameterized name parsed as dynaan-bat<N>-c<M>k-d2d<W>
# with missing segments defaulting to (bat128, c4k, d2d64). bat0 is special:
# maps to aan_policy=near (bypass filter, admit all) instead of filter.
# Examples:
#   dynaan-bat32-c16k-d2d64  -> filter, bat=32, cache=16k, d2d=64
#   dynaan-bat0-c4k          -> dynaan-nofilter (admit all), cache=4k, d2d=64
#   dynamo-d2d16             -> dynamo baseline, d2d=16
# Regex: (dynaan|dynamo)(-bat(\d+))?(-c(\d+)k)?(-d2d(\d+))?
ABLATION_POLICY_MAP = {
    # Headline perf configuration: "dynaan" is the paper's DynAAN design
    # point = filter + BAT 512 entries + 4KiB AAN cache (d2d64 default).
    "dynaan":               ("dynaan-filter", {"aan_bat_entries": 512, "aan_cache_kib": 4}),
    # A. BAT capacity sweep (bat0 = no filter; c16k/d2d64 isolate other dims)
    "dynaan-bat0-c16k":     ("dynaan-nofilter", {"aan_cache_kib": 16}),
    "dynaan-bat32-c16k":    ("dynaan-filter", {"aan_bat_entries": 32, "aan_cache_kib": 16}),
    "dynaan-bat64-c16k":    ("dynaan-filter", {"aan_bat_entries": 64, "aan_cache_kib": 16}),
    "dynaan-bat128-c16k":   ("dynaan-filter", {"aan_bat_entries": 128, "aan_cache_kib": 16}),
    "dynaan-bat256-c16k":   ("dynaan-filter", {"aan_bat_entries": 256, "aan_cache_kib": 16}),
    "dynaan-bat512-c16k":   ("dynaan-filter", {"aan_bat_entries": 512, "aan_cache_kib": 16}),
    "dynaan-bat2048-c16k":  ("dynaan-filter", {"aan_bat_entries": 2048, "aan_cache_kib": 16}),
    # B. AAN cache capacity sweep (bat2048/d2d64 = over-provisioned baseline)
    "dynaan-bat2048-c1k":   ("dynaan-filter", {"aan_bat_entries": 2048, "aan_cache_kib": 1}),
    "dynaan-bat2048-c2k":   ("dynaan-filter", {"aan_bat_entries": 2048, "aan_cache_kib": 2}),
    "dynaan-bat2048-c4k":   ("dynaan-filter", {"aan_bat_entries": 2048, "aan_cache_kib": 4}),
    "dynaan-bat2048-c8k":   ("dynaan-filter", {"aan_bat_entries": 2048, "aan_cache_kib": 8}),
    "dynaan-bat2048-c16k":  ("dynaan-filter", {"aan_bat_entries": 2048, "aan_cache_kib": 16}),
    # C. D2D bandwidth sweep (bat2048-c16k = over-provisioned AAN)
    "dynaan-bat2048-c16k-d2d16":  ("dynaan-filter", {"aan_bat_entries": 2048, "aan_cache_kib": 16, "d2d_link_width": 16}),
    "dynaan-bat2048-c16k-d2d32":  ("dynaan-filter", {"aan_bat_entries": 2048, "aan_cache_kib": 16, "d2d_link_width": 32}),
    "dynaan-bat2048-c16k-d2d64":  ("dynaan-filter", {"aan_bat_entries": 2048, "aan_cache_kib": 16, "d2d_link_width": 64}),
    "dynaan-bat2048-c16k-d2d128": ("dynaan-filter", {"aan_bat_entries": 2048, "aan_cache_kib": 16, "d2d_link_width": 128}),
    # C. DynAMO baseline at same bandwidths (isolate AAN margin from raw BW)
    "dynamo-d2d16":   ("dynamo", {"d2d_link_width": 16}),
    "dynamo-d2d32":   ("dynamo", {"d2d_link_width": 32}),
    "dynamo-d2d64":   ("dynamo", {"d2d_link_width": 64}),
    "dynamo-d2d128":  ("dynamo", {"d2d_link_width": 128}),
}


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


def base_policy_name(top_policy):
    """Map an ablation policy name to its base protocol policy."""
    if top_policy in ABLATION_POLICY_MAP:
        return ABLATION_POLICY_MAP[top_policy][0]
    return top_policy


def ablation_overrides(top_policy):
    """Knob overrides for an ablation policy name ({} for main policies)."""
    if top_policy in ABLATION_POLICY_MAP:
        return dict(ABLATION_POLICY_MAP[top_policy][1])
    return {}


def all_policy_names():
    return list(TOP_POLICY_MAP) + list(ABLATION_POLICY_MAP)


def resolve_amo_policy(
    top_policy,
    l1d_policy=None,
    aan_policy=None,
    hnf_policy=None,
):
    base = base_policy_name(top_policy)
    _validate("top AMO policy", base, TOP_POLICY_MAP)
    resolved_l1d, resolved_aan, resolved_hnf = TOP_POLICY_MAP[base]

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
        default="delegato",
        choices=all_policy_names(),
        help="Top-level AMO policy (main or ablation variant)",
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
    # Ablation knobs. None = platform default (BAT 128 entries, AAN cache
    # 4KiB, D2D 64B/flit). Ablation policy names pre-fill these through
    # apply_ablation_overrides; explicit CLI values take precedence.
    parser.add_argument(
        "--aan-bat-entries",
        type=int,
        default=None,
        help="AAN boundary admission table entries (default 128)",
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
        help="Cross-die link width in bytes/flit (default 64)",
    )
