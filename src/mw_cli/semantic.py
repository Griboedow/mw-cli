import re

from .errors import WikiError


def ask(client, query, *, all=False, max_batches=100):
    """Follow SMW's query-continue-offset without rewriting query conditions."""
    batches, seen = [], set()
    while True:
        result = client.request({"action": "ask", "query": query})
        batches.append(result)
        offset = result.get("query-continue-offset")
        if offset is None or not all or len(batches) >= max_batches:
            return {"batches": batches, "complete": offset is None, "continuation": offset}
        if not isinstance(offset, int) or offset < 0 or offset in seen:
            raise WikiError("pagination-loop", "Invalid or repeated SMW continuation offset.")
        seen.add(offset)
        query = (
            re.sub(r"\|\s*offset\s*=\s*\d+(?=\s*(?:\||$))", "", query, flags=re.IGNORECASE)
            + f"|offset={offset}"
        )
