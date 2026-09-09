from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework import status
from rest_framework.test import APITestCase

from .models import SourceCountry
from .signals import CACHE_KEY_BY_COUNTRY

User = get_user_model()


def _make_country(code="tstx", name_en="Test Country", iso="TX", active=True, order=50):
    return SourceCountry.objects.create(
        code=code,
        name_en=name_en,
        name_ar="بلد اختبار",
        iso_code=iso,
        flag_emoji="🏳️",
        latitude=0,
        longitude=0,
        avg_shipping_cost_sar=5000,
        avg_shipping_days=30,
        is_active=active,
        display_order=order,
    )


class SourceCountryModelTests(APITestCase):
    def test_str_returns_flag_and_name(self):
        c = _make_country(code="tstjp", name_en="Japan Test", iso="J1")
        c.flag_emoji = "🇯🇵"
        c.save()
        self.assertEqual(str(c), "🇯🇵 Japan Test")

    def test_default_ordering_by_display_order(self):
        # Seed data exists with orders 10-999. Use orders that interleave.
        _make_country(code="tst_b", iso="B1", order=5)
        _make_country(code="tst_a", iso="A1", order=1)
        first_two = list(SourceCountry.objects.values_list("code", flat=True)[:2])
        self.assertEqual(first_two[0], "tst_a")
        self.assertEqual(first_two[1], "tst_b")


class PublicEndpointTests(APITestCase):
    def setUp(self):
        cache.clear()
        # Seed data already exists from migration. Add one custom active + one inactive.
        self.active = _make_country(code="tst_active", name_en="Active Test", iso="T1", active=True)
        self.inactive = _make_country(code="tst_inactive", name_en="Inactive Test", iso="T2", active=False)

    def test_list_excludes_inactive(self):
        resp = self.client.get("/api/source-countries/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.data["results"] if "results" in resp.data else resp.data
        codes = [c["code"] for c in data]
        self.assertIn("tst_active", codes)
        self.assertNotIn("tst_inactive", codes)
        # Seed data should also be present
        self.assertIn("usa", codes)

    def test_retrieve_by_code(self):
        resp = self.client.get("/api/source-countries/usa/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["code"], "usa")

    def test_retrieve_inactive_returns_404(self):
        resp = self.client.get("/api/source-countries/tst_inactive/")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


class AdminEndpointTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_superuser(
            email="admin@test.com", password="pass1234", name="Admin",
        )
        self.user = User.objects.create_user(
            email="user@test.com", password="pass1234", name="User",
        )

    def test_non_admin_gets_403(self):
        self.client.force_authenticate(self.user)
        resp = self.client.post("/api/admin/source-countries/", {"code": "xx", "name_en": "X"})
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_can_create(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.post(
            "/api/admin/source-countries/",
            {
                "code": "tst_fr",
                "name_en": "France",
                "name_ar": "فرنسا",
                "iso_code": "F1",
                "flag_emoji": "🇫🇷",
                "avg_shipping_cost_sar": 7500,
                "avg_shipping_days": 28,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertTrue(SourceCountry.objects.filter(code="tst_fr").exists())

    def test_delete_is_soft(self):
        _make_country(code="tst_de", name_en="Germany Test", iso="D1")
        self.client.force_authenticate(self.admin)
        resp = self.client.delete("/api/admin/source-countries/tst_de/")
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        c = SourceCountry.objects.get(code="tst_de")
        self.assertFalse(c.is_active)

    def test_admin_write_busts_cache(self):
        cache.set(CACHE_KEY_BY_COUNTRY, {"fake": True}, 600)
        self.assertIsNotNone(cache.get(CACHE_KEY_BY_COUNTRY))
        self.client.force_authenticate(self.admin)
        self.client.post(
            "/api/admin/source-countries/",
            {
                "code": "tst_it",
                "name_en": "Italy",
                "name_ar": "إيطاليا",
                "iso_code": "I1",
                "flag_emoji": "🇮🇹",
                "avg_shipping_cost_sar": 7000,
                "avg_shipping_days": 30,
            },
            format="json",
        )
        self.assertIsNone(cache.get(CACHE_KEY_BY_COUNTRY))
