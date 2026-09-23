"""
The inspector pattern: one class per entity, one response shape for all.

A subclass says what the record is, what sits around it, what should worry a
reviewer and what they may do about it. Everything else — the envelope, the
audit trail, the admin gate, recording that the record was read — is here and
happens the same way for every entity.
"""
from django.utils import timezone


def field(key, label, value, *, display=None, internal=False, hint=None):
    """
    One row of the record.

    `value` is raw — the number, the code, the ISO timestamp — so the client
    can sort or compare it. `display` is what a human should read: the choice
    label, the formatted money. `internal=True` marks something the public API
    never returns, which the inspector shows deliberately.
    """
    return {
        'key': key,
        'label': label,
        'value': value,
        'display': display if display is not None else _default_display(value),
        'internal': internal,
        'hint': hint,
    }


def _default_display(value):
    if value is None or value == '':
        return '—'
    if value is True:
        return 'Yes'
    if value is False:
        return 'No'
    return str(value)


def section(key, title, fields, *, note=None):
    """A named group of fields. Empty groups are dropped by the view."""
    return {'key': key, 'title': title, 'note': note, 'fields': fields}


def choice_field(instance, name, label, **kwargs):
    """A model field with choices: the stored code plus its label."""
    value = getattr(instance, name, None)
    getter = getattr(instance, f'get_{name}_display', None)
    return field(name, label, value, display=getter() if getter and value else None, **kwargs)


def person(user):
    """A user, as every inspector refers to one: enough to identify and link."""
    if user is None:
        return None
    return {
        'id': user.pk,
        'name': user.name,
        'email': user.email,
        'role': user.role,
        'entity': 'user',
    }


def risk(code, severity, title, detail, *, evidence=None):
    """
    Something that should make a reviewer pause.

    `severity` is one of high / medium / low and drives nothing but ordering
    and emphasis — the inspector never blocks an action on a signal, it only
    makes sure the admin saw it.
    """
    return {
        'code': code,
        'severity': severity,
        'title': title,
        'detail': detail,
        'evidence': evidence or {},
    }


SEVERITY_ORDER = {'high': 0, 'medium': 1, 'low': 2}


def action(key, label, *, method, endpoint, available, reason=None,
           requires=None, destructive=False):
    """
    An admin action, and whether this record's state allows it right now.

    A blocked action is still returned, with the reason: an admin asking "why
    can't I approve this?" should not have to guess.
    """
    return {
        'key': key,
        'label': label,
        'method': method,
        'endpoint': endpoint,
        'available': available,
        'reason': reason,
        'requires': requires or [],
        'destructive': destructive,
    }


def duration_since(moment):
    """Hours and a human phrase since `moment`, or None."""
    if moment is None:
        return None
    delta = timezone.now() - moment
    hours = delta.total_seconds() / 3600
    if hours < 1:
        phrase = f'{int(delta.total_seconds() // 60)} minutes'
    elif hours < 48:
        phrase = f'{int(hours)} hours'
    else:
        phrase = f'{int(hours // 24)} days'
    return {'since': moment, 'hours': round(hours, 2), 'phrase': phrase}


class Inspector:
    """
    Base class. Subclasses set `entity`, `model_name` and `queryset`, and
    implement the parts that differ.
    """

    #: URL segment, e.g. 'listing' in /api/admin/inspect/listing/12/.
    entity = None
    #: How `auditlog` names this model in its rows (matched case-insensitively).
    model_name = None
    #: Human label for the entity type.
    label = None

    def __init__(self, instance, request):
        self.instance = instance
        self.request = request

    # -- what a subclass provides ------------------------------------------
    @classmethod
    def get_object(cls, pk):  # pragma: no cover - overridden
        raise NotImplementedError

    def headline(self):  # pragma: no cover - overridden
        raise NotImplementedError

    def status(self):
        return None

    def sections(self):
        return []

    def provenance(self):
        return {}

    def related(self):
        return {}

    def risk_signals(self):
        return []

    def actions(self):
        return []

    # -- the envelope, identical for every entity --------------------------
    def build(self):
        from .audit import audit_trail

        signals = sorted(
            self.risk_signals(),
            key=lambda item: SEVERITY_ORDER.get(item['severity'], 9),
        )
        return {
            'entity': self.entity,
            'entity_label': self.label,
            'id': self.instance.pk,
            'headline': self.headline(),
            'status': self.status(),
            'provenance': self.provenance(),
            'risk_signals': signals,
            'record': [s for s in self.sections() if s['fields']],
            'related': self.related(),
            'actions': self.actions(),
            'audit_trail': audit_trail(self.model_name, self.instance.pk),
        }


#: entity name -> Inspector subclass. Registered at import time by each module.
REGISTRY = {}


def register(inspector_cls):
    REGISTRY[inspector_cls.entity] = inspector_cls
    return inspector_cls
