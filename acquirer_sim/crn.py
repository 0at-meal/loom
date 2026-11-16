"""Common random numbers keyed by (seed, transaction_id, stream).

A uniform draw that depends only on the seed, the payment and the purpose of the draw, never
on how many draws came before it. Every policy routing the same payment stream therefore
meets the same issuer decisions and failures (AUDIT F-30), and a retry of a payment meets the
same issuer decision on its second acquirer.
"""

from __future__ import annotations

import hashlib

# Stream names. The issuer stream is shared by every acquirer and attempt of a payment.
ISSUER = "issuer"
SEGMENT = "segment"


def technical_stream(acquirer_id: str) -> str:
    """Stream for an acquirer's technical failure draw."""
    return f"tech:{acquirer_id}"


def latency_stream(acquirer_id: str) -> str:
    """Stream for an acquirer's latency jitter draw."""
    return f"latency:{acquirer_id}"


def crn_uniform(seed: int, transaction_id: str, stream: str) -> float:
    """Uniform draw in [0, 1) determined by ``(seed, transaction_id, stream)`` alone."""
    digest = hashlib.blake2b(f"{seed}|{transaction_id}|{stream}".encode(), digest_size=8).digest()
    # 53 high bits -> a double in [0, 1), like numpy's random()
    return (int.from_bytes(digest, "big") >> 11) * (1.0 / 9007199254740992.0)
