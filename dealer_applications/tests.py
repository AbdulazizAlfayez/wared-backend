"""
Tests for POST /api/importer-applications/ — the "Become an Importer" endpoint.

Verifies that:
  - URL, method, and request body match exactly what the frontend sends
  - Auth, duplicate, and role guards work correctly
  - Admin review endpoints function correctly
"""
from django.test import override_settings, TestCase
from rest_framework.test import APIClient

from accounts.models import User
from .models import ImporterApplication


DUMMY_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}}

URL = '/api/importer-applications/'
ADMIN_URL = '/api/importer-applications/admin/'


def make_user(email, role='user', name='Test User'):
    return User.objects.create_user(email=email, password='pw12345!', name=name, role=role)


def valid_payload(**overrides):
    """Minimal valid payload matching exactly what the frontend sends."""
    base = {
        'business_name': 'Gulf Auto Hub',
        'business_type': 'dealership',
        'city': 'Riyadh',
        'phone': '+966512345678',
    }
    base.update(overrides)
    return base


@override_settings(CACHES=DUMMY_CACHE)
class ImporterApplicationCreateTests(TestCase):
    """POST /api/importer-applications/ — applicant-facing."""

    def setUp(self):
        self.client   = APIClient()
        self.user     = make_user('user@test.com', role='user')
        self.importer = make_user('importer@test.com', role='importer')
        self.admin    = make_user('admin@test.com', role='admin')

    def auth(self, user):
        self.client.force_authenticate(user=user)

    # ── 1. Happy path: authenticated user submits valid form ──────────────
    def test_submit_creates_application_201(self):
        self.auth(self.user)
        resp = self.client.post(URL, valid_payload(), format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(ImporterApplication.objects.filter(applicant=self.user).count(), 1)

    # ── 2. Response contains expected fields ──────────────────────────────
    def test_response_fields(self):
        self.auth(self.user)
        resp = self.client.post(URL, valid_payload(), format='json')
        self.assertEqual(resp.status_code, 201)
        for field in ('id', 'business_name', 'business_type', 'city', 'phone', 'status', 'created_at'):
            self.assertIn(field, resp.data, f"Missing field: {field}")
        self.assertEqual(resp.data['status'], 'pending')

    # ── 3. Application is stored with correct data ────────────────────────
    def test_application_stored_correctly(self):
        self.auth(self.user)
        payload = valid_payload(
            business_type='showroom',
            email='biz@example.com',
            address='123 King Fahd Rd',
            description='Premium cars only',
            expected_listings='11-20',
        )
        self.client.post(URL, payload, format='json')
        app = ImporterApplication.objects.get(applicant=self.user)
        self.assertEqual(app.business_name, 'Gulf Auto Hub')
        self.assertEqual(app.business_type, 'showroom')
        self.assertEqual(app.city, 'Riyadh')
        self.assertEqual(app.email, 'biz@example.com')
        self.assertEqual(app.expected_listings, '11-20')
        self.assertEqual(app.status, 'pending')

    # ── 4. All business_type values accepted ──────────────────────────────
    def test_all_business_types_accepted(self):
        for i, btype in enumerate(['individual', 'dealership', 'showroom']):
            u = make_user(f'btype{i}@test.com', role='user')
            self.auth(u)
            resp = self.client.post(URL, valid_payload(business_type=btype), format='json')
            self.assertEqual(resp.status_code, 201, f"Failed for business_type={btype}: {resp.data}")

    # ── 5. Optional fields omitted (frontend sends undefined → omitted) ───
    def test_optional_fields_can_be_omitted(self):
        self.auth(self.user)
        payload = {
            'business_name': 'Min Motors',
            'business_type': 'individual',
            'city': 'Jeddah',
            'phone': '0501234567',
        }
        resp = self.client.post(URL, payload, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)

    # ── 6. Unauthenticated → 401 ──────────────────────────────────────────
    def test_unauthenticated_returns_401(self):
        resp = self.client.post(URL, valid_payload(), format='json')
        self.assertEqual(resp.status_code, 401, resp.data)

    # ── 7. Already an importer → 400 ─────────────────────────────────────
    def test_existing_importer_returns_400(self):
        self.auth(self.importer)
        resp = self.client.post(URL, valid_payload(), format='json')
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn('importer access', resp.data['detail'])

    # ── 8. Already an admin → 400 ────────────────────────────────────────
    def test_existing_admin_returns_400(self):
        self.auth(self.admin)
        resp = self.client.post(URL, valid_payload(), format='json')
        self.assertEqual(resp.status_code, 400, resp.data)

    # ── 9. Duplicate pending application → 400 ───────────────────────────
    def test_duplicate_pending_application_returns_400(self):
        self.auth(self.user)
        self.client.post(URL, valid_payload(), format='json')
        resp = self.client.post(URL, valid_payload(business_name='Second Try'), format='json')
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn('pending', resp.data['detail'])

    # ── 10. Can reapply after rejection ──────────────────────────────────
    def test_can_reapply_after_rejection(self):
        ImporterApplication.objects.create(
            applicant=self.user,
            business_name='Old Biz',
            business_type='individual',
            city='Riyadh',
            phone='0501111111',
            status='rejected',
        )
        self.auth(self.user)
        resp = self.client.post(URL, valid_payload(), format='json')
        self.assertEqual(resp.status_code, 201, resp.data)

    # ── 11. Missing required field business_name → 400 ───────────────────
    def test_missing_business_name_returns_400(self):
        self.auth(self.user)
        payload = valid_payload()
        del payload['business_name']
        resp = self.client.post(URL, payload, format='json')
        self.assertEqual(resp.status_code, 400)

    # ── 12. Missing required field city → 400 ────────────────────────────
    def test_missing_city_returns_400(self):
        self.auth(self.user)
        payload = valid_payload()
        del payload['city']
        resp = self.client.post(URL, payload, format='json')
        self.assertEqual(resp.status_code, 400)

    # ── 13. Missing required field phone → 400 ───────────────────────────
    def test_missing_phone_returns_400(self):
        self.auth(self.user)
        payload = valid_payload()
        del payload['phone']
        resp = self.client.post(URL, payload, format='json')
        self.assertEqual(resp.status_code, 400)

    # ── 14. Invalid business_type → 400 ──────────────────────────────────
    def test_invalid_business_type_returns_400(self):
        self.auth(self.user)
        resp = self.client.post(URL, valid_payload(business_type='unknown'), format='json')
        self.assertEqual(resp.status_code, 400)

    # ── 15. expected_listings values accepted ─────────────────────────────
    def test_expected_listings_values(self):
        for i, val in enumerate(['1-5', '6-10', '11-20', '21-50', '50+']):
            u = make_user(f'el{i}@test.com', role='user')
            self.auth(u)
            resp = self.client.post(URL, valid_payload(expected_listings=val), format='json')
            self.assertEqual(resp.status_code, 201, f"Failed for expected_listings={val}: {resp.data}")


@override_settings(CACHES=DUMMY_CACHE)
class ImporterApplicationAdminTests(TestCase):
    """Admin review: list, approve, reject."""

    def setUp(self):
        self.client    = APIClient()
        self.applicant = make_user('applicant@test.com', role='user', name='Applicant')
        self.admin     = make_user('admin@test.com', role='admin', name='Admin')
        self.app = ImporterApplication.objects.create(
            applicant=self.applicant,
            business_name='Test Motors',
            business_type='dealership',
            city='Dammam',
            phone='0509999999',
            status='pending',
        )

    def auth(self, user):
        self.client.force_authenticate(user=user)

    def _results(self, resp):
        """Handle both paginated {count, results} and plain list responses."""
        if isinstance(resp.data, dict) and 'results' in resp.data:
            return resp.data['results']
        return resp.data

    # ── 16. Admin can list all applications ───────────────────────────────
    def test_admin_can_list_applications(self):
        self.auth(self.admin)
        resp = self.client.get(ADMIN_URL)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(self._results(resp)), 1)

    # ── 17. Non-admin gets empty list ─────────────────────────────────────
    def test_non_admin_gets_empty_list(self):
        self.auth(self.applicant)
        resp = self.client.get(ADMIN_URL)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(self._results(resp)), 0)

    # ── 18. Admin can filter by status ────────────────────────────────────
    def test_admin_filter_by_status(self):
        self.auth(self.admin)
        resp = self.client.get(f'{ADMIN_URL}?status=pending')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(self._results(resp)), 1)
        resp2 = self.client.get(f'{ADMIN_URL}?status=approved')
        self.assertEqual(len(self._results(resp2)), 0)

    # ── 19. Admin approves → applicant role becomes importer ─────────────
    def test_admin_approve_promotes_to_importer(self):
        self.auth(self.admin)
        resp = self.client.patch(
            f'{ADMIN_URL}{self.app.pk}/',
            {'status': 'approved', 'admin_notes': 'Looks good'},
            format='json',
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, 'approved')
        self.applicant.refresh_from_db()
        self.assertEqual(self.applicant.role, 'importer')

    # ── 20. Admin rejects → applicant role unchanged ──────────────────────
    def test_admin_reject_does_not_change_role(self):
        self.auth(self.admin)
        self.client.patch(
            f'{ADMIN_URL}{self.app.pk}/',
            {'status': 'rejected', 'admin_notes': 'Incomplete docs'},
            format='json',
        )
        self.applicant.refresh_from_db()
        self.assertEqual(self.applicant.role, 'user')

    # ── 21. Unauthenticated cannot access admin list → 401 ────────────────
    def test_unauthenticated_cannot_access_admin(self):
        resp = self.client.get(ADMIN_URL)
        self.assertEqual(resp.status_code, 401)
