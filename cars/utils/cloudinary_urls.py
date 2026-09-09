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
