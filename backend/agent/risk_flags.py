# backend/agent/risk_flags.py
# The server decides the risk flags. An API caller may never supply them.


def compute_flags(candidates, source):
    """Return {product_id: (flag, ...)} for the products being approved.

    candidates: a list of models.Candidate (a Product plus the Offer we would buy).
    source:     "demo" or "channel3", where the products came from.

    The API must never take flags from the client: a caller could clear them and
    skip the step-up confirmation. This function is the only place flags are made.
    """
    return {}
