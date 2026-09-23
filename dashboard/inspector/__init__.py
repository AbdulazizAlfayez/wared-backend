"""
The admin inspector: the complete internal record of one entity.

An admin reviewing a car was being sent to `/car/{id}` — the buyer's page,
with a Reserve button and a marketing layout. This package answers the
question that page cannot: everything the platform knows about this record,
who touched it, what is related to it, what should make a reviewer pause, and
what they are allowed to do about it.

One inspector class per entity type, registered by name, served by one view at
`/api/admin/inspect/<entity>/<id>/`. Adding an entity is a class, not an
endpoint.

The serializers here are admin-only and deliberately separate from the public
ones: nothing in this package is imported by a public serializer, and the
public serializers are never widened to serve it.
"""
