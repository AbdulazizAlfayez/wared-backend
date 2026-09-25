"""
Cloudinary URL transformation helper.

Inserts on-the-fly transformation parameters into a Cloudinary delivery
URL without re-uploading.  The original image is never modified.

Usage:
    from cars.utils.cloudinary_urls import image_variants

    variants = image_variants("https://res.cloudinary.com/.../upload/v1/listings/abc.jpg")
    # {'thumb': '...160x120...', 'card': '...640x480...', 'full': '...w_1600...'}
"""

# Named presets — (width, height, crop_mode)
VARIANTS = {
    'thumb': 'w_160,h_120,c_fill,f_auto,q_auto',
    'card':  'w_640,h_480,c_fill,f_auto,q_auto',
    'full':  'w_1600,c_limit,f_auto,q_auto',
}


def cloudinary_transform(url: str, transform: str) -> str:
    """Insert a Cloudinary transformation into *url*.

    Works with URLs of the form:
        https://res.cloudinary.com/<cloud>/image/upload[/existing_transforms]/v<N>/<path>
    """
    if not url or '/upload/' not in url:
        return url
    return url.replace('/upload/', f'/upload/{transform}/', 1)


def image_variants(url: str | None) -> dict[str, str | None]:
    """Return a dict of {thumb, card, full} URLs for a Cloudinary image.

    Returns all-None dict if *url* is falsy.
    """
    if not url:
        return {'thumb': None, 'card': None, 'full': None}
    return {
        name: cloudinary_transform(url, tx)
        for name, tx in VARIANTS.items()
    }


def primary_image_variants(listing, sizes=('thumb', 'card')) -> dict[str, str | None]:
    """Return the requested size variants for a listing's primary image.

    Uses the prefetched `images` queryset when available to avoid extra queries.
    """
    images = listing.images.all()
    primary = None
    for img in images:
        if img.is_primary:
            primary = img
            break
    if primary is None and images:
        primary = images[0]

    if not primary or not primary.image:
        return {s: None for s in sizes}

    url = primary.image.url
    return {
        name: cloudinary_transform(url, VARIANTS[name])
        for name in sizes
        if name in VARIANTS
    }


def primary_image_payload(listing) -> dict[str, str] | None:
    """
    The one shape every serializer gives a listing's primary image.

    `{thumb, card, full}` when there is a photo, `None` when there is not —
    and never a half-and-half. Clients used to meet four shapes for the same
    key: this dict from `/api/listings/`, a bare full-size URL from the detail,
    `my`, `compare`, `featured` and the importer desk, the same URL again under
    `primary_image_url` on conversations and reservations, and `null`. The
    mobile card read `primary_image.card` and crashed on the desk, where a car
    with no photos arrives as `null`.

    `None` rather than a dict of nulls, because a caller asking "is there a
    photo" should not have to look inside to find out; the clients' own helper
    turns either into a placeholder.

    Uses the prefetched `images` queryset when there is one, so a list of forty
    cards stays one query.
    """
    images = listing.images.all() if listing is not None else []
    primary = None
    for image in images:
        if image.is_primary:
            primary = image
            break
    if primary is None:
        primary = next(iter(images), None)

    if primary is None or not primary.image:
        return None

    try:
        url = primary.image.url
    except Exception:
        # A CloudinaryField holding a malformed value raises on `.url`. A
        # missing photo must not take an endpoint down with it.
        return None

    if not url:
        return None
    return image_variants(url)


def primary_image_url(listing) -> str | None:
    """
    The bare URL, for the `primary_image_url` fields that already ship it.

    Kept alongside `primary_image_payload` rather than replaced: clients read
    this key today, and removing it to tidy the API would break them for no
    gain. It is the `full` variant rather than the untransformed original —
    nothing wants a 4000px original in a chat header.
    """
    payload = primary_image_payload(listing)
    return payload['full'] if payload else None
