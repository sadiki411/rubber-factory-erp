"""Measurement display/relationship rules. Never post quantities or rewrite facts."""


def applicable_criteria(criteria, stage=None):
    from .limits import limit_mode
    return [c for c in criteria if (stage is None or c["stage"] == stage) and limit_mode(c) != "NONE"]


def same_scope(left, right):
    keys = ("criterion_id", "measurement_object", "bake_event_id", "sample_ref", "material_ref")
    return (left.kind == right.kind and left.scope.strip() == right.scope.strip()
            and all(left.values.get(k) == right.values.get(k) for k in keys)
            and {c.pk for c in left.cards.all()} == {c.pk for c in right.cards.all()})


def later_minute(left, right):
    # The ordinary form records minutes. Hidden seconds/insert order are not
    # evidence that two readings in the same displayed minute are sequential.
    return bool(left.business_at and right.business_at and
                left.business_at.replace(second=0, microsecond=0) > right.business_at.replace(second=0, microsecond=0))


def reviewed_measurements(events):
    by_id = {e.pk: e for e in events}
    parents = {e.replaces_id for e in events if e.replaces_id}
    reviewed = set()
    for leaf in events:
        if leaf.pk in parents or leaf.values.get("judgment") not in ("WITHIN", "OUTSIDE"):
            continue
        child = leaf
        visited = set()
        while child.replaces_id in by_id and child.replaces_id not in visited:
            parent = by_id[child.replaces_id]
            visited.add(parent.pk)
            if not same_scope(leaf, parent):
                break
            # A valid correction still supersedes the original fact after a
            # later retest of that correction. Compare retests by business
            # time, not with the superseded (possibly mistyped) timestamp.
            correction_edge = (child.values.get("review_mode") == "CORRECTION"
                               and bool(child.values.get("correction_reason")))
            child_resolved = child.pk == leaf.pk or child.pk in reviewed
            if (correction_edge and child_resolved) or later_minute(leaf, parent):
                reviewed.add(parent.pk)
            child = parent
    return reviewed
