"""
Which edits to an approved listing send it back to WARED.

An approved listing is a promise to buyers: this car, these photographs, this
price. Changing any part of that promise has to be looked at again. Fixing a
typo in the description does not.

The split is deliberately conservative on the money and identity fields — the
ones a bait-and-switch would use — and permissive on everything a seller might
reasonably correct after approval.
"""

#: Editing any of these on an approved listing returns it to review.
REVIEW_TRIGGERING_FIELDS = frozenset({
    # Identity: a different car entirely.
    'make',
    'model',
    'year',
    'vin',
    # Money: what the buyer would pay, and every input that derives it.
    'price',
    'final_price_sar',
    'source_price',
    'source_currency',
    'shipping_cost',
    'customs_duty_amount',
    'vat_amount',
    'inspection_fee',
    'transportation_cost',
})

#: Fields an owner may correct on an approved listing without re-review.
#: Not enforced as a whitelist — anything outside REVIEW_TRIGGERING_FIELDS is
#: allowed — but named here so the intent is readable and testable.
FREELY_EDITABLE_EXAMPLES = frozenset({
    'description',
    'mileage',
    'city',
    'color',
    'accident_description',
    'damage_description',
})


def _normalise(value):
    """Compare 3 and Decimal('3.00') as equal; None and '' as equal."""
    if value is None or value == '':
        return None
    try:
        from decimal import Decimal

        return Decimal(str(value)).normalize()
    except Exception:
        return str(value)


def changed_review_fields(before: dict, after: dict) -> set:
    """The review-triggering fields whose value actually differs."""
    changed = set()
    for field in REVIEW_TRIGGERING_FIELDS:
        if field not in before and field not in after:
            continue
        if _normalise(before.get(field)) != _normalise(after.get(field)):
            changed.add(field)
    return changed


def requires_rereview(status: str, before: dict, after: dict, photos_changed: bool = False) -> bool:
    """
    Whether this edit puts the listing back in the queue.

    Only approved listings are affected: a pending one is already in the queue,
    and a rejected or changes_requested one is resubmitted by the existing rule
    in `ListingViewSet.update`.
    """
    if status != 'approved':
        return False
    return photos_changed or bool(changed_review_fields(before, after))
