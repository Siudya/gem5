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
}

TOP_POLICY_MAP = {
    "all-near": ("near", "bypass", "central"),
    "all-central": ("unique-near", "bypass", "central"),
    "all-migrate": ("unique-near", "bypass", "migrate"),
    "dynamo": ("dynamo", "bypass", "central"),
    "pc": ("unique-near", "bypass", "pc"),
    "po": ("unique-near", "bypass", "po"),
    "ca": ("unique-near", "bypass", "ca"),
    "delegato": ("unique-near", "bypass", "delegato"),
    "dynaan": ("dynamo", "near", "central"),
    "dynaan-filter": ("dynamo", "filter", "central"),
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


def resolve_amo_policy(
    top_policy,
    l1d_policy=None,
    aan_policy=None,
    hnf_policy=None,
):
    _validate("top AMO policy", top_policy, TOP_POLICY_MAP)
    resolved_l1d, resolved_aan, resolved_hnf = TOP_POLICY_MAP[top_policy]

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


def add_amo_policy_args(parser):
    parser.add_argument(
        "--amo-policy",
        type=str,
        default="delegato",
        choices=list(TOP_POLICY_MAP),
        help="Top-level AMO policy",
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
