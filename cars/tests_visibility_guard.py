"""
Guard: no public-facing view may build a Listing queryset of its own.

Reserved cars stay private only because every public surface filters on
cars.visibility.public_market_q(). A new endpoint that writes
`Listing.objects.filter(status='approved')` by hand would leak them again,
silently, and no functional test would notice until a car leaked in
production.

So this scans the modules that serve listings and fails when a
`Listing.objects...` expression appears in a function that never mentions
public_market_q. Adding a genuinely non-public query (owner-scoped, admin,
bulk tools) means adding it to ALLOWED below, with a reason — a deliberate
act, not an accident.
"""
import ast
import pathlib

from django.test import SimpleTestCase

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

#: Modules that serve listings to users.
SCANNED_MODULES = (
    'cars/views.py',
    'cars/serializers.py',
    'cars/filter_options.py',
    'cars/filters.py',
    'source_countries/views.py',
    'importers/views.py',
    'importers/serializers.py',
    'favorites/views.py',
    'social/visibility.py',
    'social/views.py',
    'assistant/services.py',
    'dashboard/views.py',
    'dashboard/analytics.py',
)

#: (module, qualified name) → why this query does not need public_market_q.
ALLOWED = {
    ('cars/views.py', '_track_view'):
        'View counter, updates by pk. Returns no listing data.',
    ('cars/views.py', 'ListingViewSet'):
        'Class-level `queryset` for the router only; get_queryset() overrides '
        'it on every request and does filter on public_market_q.',
    ('cars/views.py', 'ListingViewSet.my_listings'):
        "GET /api/listings/my/ — the caller's own listings.",
    ('cars/views.py', 'BulkStatusChangeView.post'):
        'Bulk tool, owner- or admin-scoped writes.',
    ('cars/views.py', 'BulkDeleteView.post'):
        'Bulk tool, owner- or admin-scoped writes.',
    ('cars/views.py', 'BulkExportView.get'):
        "Bulk export of the caller's own listings (admins: all).",
    ('cars/serializers.py', 'ListingSerializer.validate'):
        "Duplicate-VIN check against the owner's own listings.",
    ('importers/views.py', 'AdminCRApproveView.post'):
        "Admin action: un-archives the importer's own listings.",
    ('dashboard/views.py', 'DealerDashboardView.get'):
        'Dealer dashboard — own listings, role-gated.',
    ('dashboard/views.py', 'AdminDashboardView.get'):
        'Admin dashboard, staff only.',
    ('dashboard/views.py', 'SingleListingAnalyticsView.get'):
        'Analytics for one listing; owner or admin only.',
    ('dashboard/analytics.py', 'get_dealer_analytics_overview'):
        'Dealer analytics — own listings, role-gated.',
    ('dashboard/analytics.py', 'get_dealer_listing_analytics'):
        'Dealer analytics — own listings, role-gated.',
    ('dashboard/analytics.py', 'get_comparative_analytics'):
        'Dealer vs platform aggregates, role-gated; no listing is identified.',
    ('dashboard/analytics.py', 'get_admin_platform_analytics'):
        'Platform totals, staff only.',
    ('dashboard/analytics.py', 'get_admin_platform_analytics._top_field'):
        'Platform totals, staff only.',
    ('dashboard/analytics.py', 'get_top_performing_listings'):
        'Admin/dealer leaderboard, role-gated.',
}


def _scope_chain(node, parents):
    chain = []
    cur = node
    while cur in parents:
        cur = parents[cur]
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            chain.append(cur)
    return list(reversed(chain))


def find_unguarded():
    """Returns [(module, qualname, lineno, source)] for every unguarded query."""
    findings = []
    for module in SCANNED_MODULES:
        path = REPO_ROOT / module
        source = path.read_text()
        tree = ast.parse(source)

        parents = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node

        seen = set()
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            segment = (ast.get_source_segment(source, node) or '').replace('\n', ' ')
            if 'Listing.objects' not in segment:
                continue

            chain = _scope_chain(node, parents)
            qualname = '.'.join(c.name for c in chain) or '<module>'
            if (module, qualname) in seen:
                continue
            seen.add((module, qualname))

            # Guarded if public_market_q appears anywhere in the enclosing
            # function — `base = Listing.objects...` then `base.filter(
            # public_market_q(user))` is a normal, correct shape.
            enclosing = next(
                (c for c in reversed(chain)
                 if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef))),
                None,
            )
            scope_src = (
                ast.get_source_segment(source, enclosing) if enclosing else segment
            ) or ''
            if 'public_market_q' in scope_src:
                continue
            if (module, qualname) in ALLOWED:
                continue
            findings.append((module, qualname, node.lineno, ' '.join(segment.split())[:120]))
    return findings


class PublicQuerysetGuardTests(SimpleTestCase):

    def test_every_public_listing_queryset_goes_through_public_market_q(self):
        unguarded = find_unguarded()
        self.assertEqual(unguarded, [], '\n'.join(
            [
                'These build a Listing queryset without cars.visibility.public_market_q().',
                'Filter on it, or add the entry to ALLOWED in this file with a reason:',
                '',
            ] + [f'  {m}:{line}  {qual}()  →  {src}' for m, qual, line, src in unguarded]
        ))

    def test_the_guard_actually_catches_a_bypass(self):
        # A module not in ALLOWED with a hand-rolled public query must fail.
        import tempfile

        global SCANNED_MODULES
        with tempfile.NamedTemporaryFile('w', suffix='.py', dir=REPO_ROOT, delete=True) as fh:
            fh.write(
                'from cars.models import Listing\n\n'
                'def leaky_view(request):\n'
                "    return Listing.objects.filter(status='approved')\n"
            )
            fh.flush()
            name = pathlib.Path(fh.name).name
            original = SCANNED_MODULES
            SCANNED_MODULES = original + (name,)
            try:
                found = [f for f in find_unguarded() if f[0] == name]
            finally:
                SCANNED_MODULES = original
        self.assertEqual(len(found), 1, found)
        self.assertEqual(found[0][1], 'leaky_view')

    def test_allowlist_has_no_stale_entries(self):
        """An ALLOWED entry that no longer matches any query should be removed."""
        live = set()
        for module in SCANNED_MODULES:
            source = (REPO_ROOT / module).read_text()
            tree = ast.parse(source)
            parents = {}
            for node in ast.walk(tree):
                for child in ast.iter_child_nodes(node):
                    parents[child] = node
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                    continue
                segment = (ast.get_source_segment(source, node) or '').replace('\n', ' ')
                if 'Listing.objects' not in segment:
                    continue
                chain = _scope_chain(node, parents)
                live.add((module, '.'.join(c.name for c in chain) or '<module>'))
        self.assertEqual(sorted(set(ALLOWED) - live), [])
