"""The audit trail, as an inspector shows it."""
from auditlog.models import AuditLog


def _as_mapping(value):
    """
    Audit rows do not agree on a shape.

    Call sites write a full snapshot dict, a single-key dict, or a bare string
    (`orders/tasks.py` logs `old_value=old_status`). A diff renderer has to
    survive all three, so anything that is not a mapping is presented as one
    field called "value".
    """
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    return {'value': value}


def diff_of(old_value, new_value):
    """
    Field-level before → after for one audit row.

    Only fields that actually differ are returned; a snapshot pair usually
    holds a dozen identical fields and one real change, and showing all of
    them buries it.
    """
    old, new = _as_mapping(old_value), _as_mapping(new_value)
    changes = []
    for field in sorted(set(old) | set(new)):
        before, after = old.get(field), new.get(field)
        if before == after:
            continue
        changes.append({'field': field, 'before': before, 'after': after})
    return changes


def actor_of(entry):
    if entry.user is None:
        # Cron, Celery and management commands log with no user.
        return None
    return {
        'id': entry.user.pk,
        'name': entry.user.name,
        'email': entry.user.email,
        'role': entry.user.role,
    }


def audit_trail(model_name, object_id, limit=200):
    """
    Every recorded event for one object, newest first.

    `model_name` is free text on the row and has been written both cased and
    lowercased over the years ('Listing' 11 times, 'listing' 120), so the
    match is case-insensitive — otherwise half a car's history disappears.
    """
    entries = (
        AuditLog.objects
        .select_related('user')
        .filter(model_name__iexact=model_name, object_id=object_id)
        .order_by('-timestamp')[:limit]
    )
    return [
        {
            'id': entry.pk,
            'action': entry.action,
            'action_display': entry.get_action_display() or entry.action,
            'actor': actor_of(entry),
            'timestamp': entry.timestamp,
            'ip_address': entry.ip_address,
            'user_agent': entry.user_agent or None,
            'changes': diff_of(entry.old_value, entry.new_value),
        }
        for entry in entries
    ]
