"""Participation assignment, independent of workload and transport randomness."""

from collections import deque

from starksim.derive import stable_fraction


AGGREGATOR_PLACEMENTS = ("random", "central")
RELAY_FORWARDING_RULES = ("best", "all", "none")
PROPOSER_ROLES = ("as_assigned", "aggregator", "relay")


def _closeness(overlay, origin):
    """BFS closeness, scaled by reachable fraction on disconnected overlays."""
    distances = {origin: 0}
    pending = deque([origin])
    while pending:
        node = pending.popleft()
        for peer in overlay.peers(node):
            if peer not in distances:
                distances[peer] = distances[node] + 1
                pending.append(peer)
    reached = len(distances) - 1
    if not reached:
        return 0.0
    # The usual (N-1)/sum(distance) on connected graphs. The reachable
    # fraction prevents a tiny disconnected component from looking central.
    return reached * reached / ((overlay.node_count - 1)
                                * sum(distances.values()))


def assign_aggregators(overlay, seed, share, placement, proposer_role,
                       proposer_id):
    """Return aggregator ids; swaps follow the same ranking as placement.

    Full participation needs no ranking, hashing or traversal. A forced relay
    with no relay slot is impossible while preserving the requested count.
    """
    count = max(1, round(share * overlay.node_count))
    if count == overlay.node_count:
        if proposer_role == "relay":
            raise ValueError("proposer_role='relay' needs a relay slot: lower "
                             "aggregator_share so fewer than all nodes aggregate")
        return frozenset(range(overlay.node_count))
    if placement == "random":
        ranking = sorted(range(overlay.node_count), key=lambda node: (
            stable_fraction(seed, "aggregator", node), node))
    else:
        ranking = sorted(range(overlay.node_count), key=lambda node: (
            -_closeness(overlay, node), node))
    aggregators = set(ranking[:count])
    if proposer_role == "aggregator" and proposer_id not in aggregators:
        # Displace the lowest-ranked selected node, preserving the strongest
        # placement candidates compatible with the proposer's forced role.
        aggregators.remove(ranking[count - 1])
        aggregators.add(proposer_id)
    elif proposer_role == "relay" and proposer_id in aggregators:
        aggregators.remove(proposer_id)
        aggregators.add(ranking[count])
    return frozenset(aggregators)
