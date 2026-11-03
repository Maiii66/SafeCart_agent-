# backend/agent/risk_flags.py
# The server decides the risk flags. An API caller may never supply them.
#
# The API must never take flags from a client: a caller could clear them and skip
# the step-up confirmation. This module reads only flags the server itself saved
# during a recent search.
#
# Honest limit: a product that was never returned by a recent search has no flags.
# The price flag is a warning for the person, not one of the money limits.

import time

from database import repository

FLAG_MEMORY_SECONDS = 7200


def compute_flags(candidates, source, now=None):
    """Return {product_id: (flag, ...)} for products seen in the last two hours.

    candidates: a list of models.Candidate (a Product plus the Offer we would buy).
    source:     "demo" or "channel3", where the products came from.
    now:        the current time in seconds, for tests.
    """
    now = time.time() if now is None else now
    since = int(now) - FLAG_MEMORY_SECONDS

    result = {}
    for candidate in candidates:
        product_id = candidate.product.id
        flags = repository.get_product_flags(product_id, source, since)
        if flags:
            result[product_id] = tuple(flags)
    return result
