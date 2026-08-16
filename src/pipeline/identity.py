"""Domain comparison for identity resolution.

Merging two addresses under one person on an identical display name is how the
planted `same_person_two_addresses` case gets solved. Done naively it also
solves the `lookalike_domain` fraud case the wrong way round: an impersonator
reuses their target's display name, so a bare name match folds the attacker
into the very person they are impersonating and the fraud signal disappears.

The discriminator is registrable domain:

    email.united.com  vs united.com  -> same registrable domain, a subdomain.
                                        Legitimate vendor. Merge.
    klaviyo-billing.com vs klaviyo.com -> DIFFERENT registrable domains, but one
                                        SLD contains the other. Impersonation.
                                        Do not merge; record the conflict.
    gmail.com vs pembertonwells.com  -> unrelated. Personal + work address for
                                        one human. Merge.
"""

from __future__ import annotations

import re

# Public suffixes that need three labels to reach the registrable domain.
# Only the ones this corpus can plausibly hit; not a full PSL.
_TWO_PART_SUFFIXES = {
    "co.uk", "org.uk", "ac.uk", "gov.uk", "co.jp", "co.in",
    "com.au", "co.nz", "com.br", "co.za",
}


def registrable(domain: str) -> str:
    """Best-effort registrable domain (eTLD+1)."""
    parts = domain.lower().strip().split(".")
    if len(parts) < 2:
        return domain.lower().strip()
    if ".".join(parts[-2:]) in _TWO_PART_SUFFIXES and len(parts) >= 3:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def sld(domain: str) -> str:
    """The name label of the registrable domain, without its suffix."""
    return registrable(domain).split(".")[0]


def _has_token(haystack: str, needle: str) -> bool:
    """Is `needle` a whole hyphen/dot-delimited token inside `haystack`?"""
    return needle in [t for t in re.split(r"[-_.]+", haystack) if t]


def _edit_distance(a: str, b: str, cap: int = 3) -> int:
    """Levenshtein distance, short-circuited once it exceeds `cap`."""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for jx, cb in enumerate(b, 1):
            cur.append(min(prev[jx] + 1, cur[jx - 1] + 1, prev[jx - 1] + (ca != cb)))
        if min(cur) > cap:
            return cap + 1
        prev = cur
    return prev[-1]


# Relations that block a name-based merge, strongest evidence first.
LOOKALIKE_RELATIONS = ("embedded_brand", "typosquat", "sibling_tld")


def domain_relation(a: str, b: str) -> str:
    """Classify two email domains.

    Returns 'same' | 'embedded_brand' | 'typosquat' | 'sibling_tld' | 'unrelated'.
    The three middle values are graded by how strongly they suggest
    impersonation rather than a second domain owned by the same organisation.
    """
    ra, rb = registrable(a), registrable(b)
    if ra == rb:
        return "same"

    sa, sb = sld(ra), sld(rb)
    longer, shorter = (sa, sb) if len(sa) >= len(sb) else (sb, sa)

    # One brand name embedded in another registrable domain, AT A TOKEN
    # BOUNDARY: klaviyo-billing / klaviyo, harborlinebank-support /
    # harborlinebank, kettlehq-billing / kettlehq. Strongest signal — this is
    # the shape invoice-fraud domains actually take.
    #
    # The boundary matters. A bare substring test also fires on vanta.com vs
    # vantageassurance.com and google.com vs googlemail.com — one unrelated,
    # one legitimately the same company. Real impersonation appends a word to
    # the brand; it does not bury the brand inside a longer one.
    if sa != sb and len(shorter) >= 4 and _has_token(longer, shorter):
        return "embedded_brand"

    # Near-miss spellings: a transposition or a dropped letter.
    if sa != sb and len(shorter) >= 6 and _edit_distance(sa, sb) <= 2:
        return "typosquat"

    # Same brand, different public suffix: klaviyo.com vs klaviyo.net.
    # Genuinely ambiguous — docusign.com and docusign.net are both DocuSign,
    # but this is also how a cheap impersonation looks. Flagged weakly.
    if sa == sb:
        return "sibling_tld"

    return "unrelated"


def may_merge(addr_a: str, addr_b: str) -> tuple[bool, str]:
    """Should these two addresses resolve to the same person?

    Returns (allowed, relation). Any lookalike relation blocks the merge:
    splitting a pair that was really one party costs a little recall, while
    merging an impersonator into their target destroys the fraud signal
    outright. The asymmetry is the whole point.
    """
    dom_a = addr_a.split("@")[-1]
    dom_b = addr_b.split("@")[-1]
    relation = domain_relation(dom_a, dom_b)
    return relation not in LOOKALIKE_RELATIONS, relation
