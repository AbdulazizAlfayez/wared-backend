"""
The admin inspector: who may read it, and whether it tells the truth.

Two things here are not ordinary feature tests. `test_public_payload_is_byte_
for_byte_what_it_was` freezes the anonymous listing payload so that widening
the public serializer to feed the inspector fails the build instead of
shipping. And the 403 tests loop over the registry, so a new entity inspector
cannot be added without being covered.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from auditlog.models import AuditLog
from cars.models import Listing
from cars.serializers import ListingSerializer
from dashboard.inspector.base import REGISTRY
from dashboard.inspector.views import InspectView  # noqa: F401 — fills REGISTRY
from importers.models import ImporterProfile

User = get_user_model()


#: Every key an anonymous caller gets from `GET /api/listings/{id}/`, frozen on
#: 2026-09-20, before the inspector existed. The inspector reads fields no
#: buyer may see; if one of them ever appears here, something widened the
#: public serializer and this test is the tripwire.
#:
#: Changed deliberately once, on 2026-09-24: `is_owner` was added to the
#: listing serializers so a client can draw its own cars as "yours" without
#: comparing owner_id itself. It is per-viewer but it is not private — it tells
#: you only about yourself, is False for everyone anonymous, and costs no
#: query. It is in this set because it was a decision, not a leak. Anything
#: else that turns up here still needs explaining.
PUBLIC_LISTING_FIELDS = frozenset({
    'accident_description', 'accident_history', 'active_promotion',
    'actual_arrival_date', 'approved_at', 'approved_by', 'auction_lot_number',
    'auction_source', 'autocheck_url', 'bill_of_lading_number', 'body_type',
    'carfax_url', 'city', 'city_ar', 'city_display', 'city_obj', 'color',
    'color_ar', 'color_display', 'color_interior', 'color_interior_ar',
    'comment_count', 'condition', 'conformity_certificate_number',
    'container_number', 'cost_breakdown', 'created_at',
    'customs_clearance_date', 'customs_cleared', 'customs_declaration_number',
    'customs_duty_amount', 'cylinders', 'damage_description', 'description',
    'description_ar', 'description_display', 'doors', 'drive_type',
    'emissions_compliant', 'engine_size', 'estimated_arrival_date',
    'final_price_sar', 'fuel_type', 'gcc_specs', 'has_flood_damage',
    'has_frame_damage', 'has_salvage_title', 'horsepower', 'id', 'images',
    'import_status', 'imported_from', 'inspection_fee', 'is_active',
    'is_favorited_by_me', 'is_featured', 'is_highlighted', 'is_homepage',
    'is_liked', 'is_owner', 'is_promoted', 'is_reserved', 'is_top_search',
    'latitude',
    'like_count', 'longitude', 'make', 'make_ar', 'make_display', 'mileage',
    'model', 'model_ar', 'model_display', 'negotiable', 'odometer_verified',
    'original_listing_url', 'owner', 'owner_id', 'owner_verification_level',
    'owner_verified', 'port_of_entry', 'port_of_origin', 'price',
    'primary_image', 'promotion_priority', 'region_display',
    'reservation_state', 'seats', 'service_history', 'shipping_cost',
    'shipping_line', 'showroom', 'source_city', 'source_country',
    'source_currency', 'source_price', 'spec_origin', 'status',
    'status_changed_at', 'submitted_at', 'title', 'total_landed_cost',
    'transmission', 'transportation_cost', 'unique_view_count', 'vat_amount',
    'vehicle_inspection_date', 'vehicle_inspection_result', 'vessel_name',
    'view_count', 'view_stats', 'vin', 'warranty_remaining', 'workshop',
    'year',
})


class InspectorTestBase(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            email='inspector-admin@test.sa', password='Passw0rd!x',
            name='Inspector Admin', role='admin',
        )
        cls.importer = User.objects.create_user(
            email='inspector-importer@test.sa', password='Passw0rd!x',
            name='Inspected Importer', role='importer', is_business_verified=True,
        )
        cls.buyer = User.objects.create_user(
            email='inspector-buyer@test.sa', password='Passw0rd!x',
            name='Curious Buyer', role='user',
        )
        # A signal gives every importer a profile on creation, so this updates
        # the one that already exists rather than making a second.
        cls.profile, _ = ImporterProfile.objects.update_or_create(
            user=cls.importer,
            defaults={
                'business_name': 'Inspected Imports',
                'cr_verification_status': 'verified',
                'cr_expiry_date': timezone.localdate() + timedelta(days=200),
            },
        )
        cls.listing = Listing.objects.create(
            owner=cls.importer, make='Audi', model='RS6', year=2024,
            price=420000, mileage=12000, city='Riyadh', status='pending',
            vin='WUAZZZ4F98N901234', submitted_at=timezone.now(),
        )

    def url(self, entity='listing', pk=None):
        return reverse('admin-inspect', args=[entity, pk or self.listing.pk])

    def inspect(self, entity='listing', pk=None):
        self.client.force_authenticate(user=self.admin)
        return self.client.get(self.url(entity, pk))


class InspectorAccessTests(InspectorTestBase):
    """Admin only, and 403 rather than a thinner answer."""

    def test_admin_can_read_the_record(self):
        response = self.inspect()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['entity'], 'listing')
        self.assertEqual(response.data['id'], self.listing.pk)

    def test_buyer_is_refused_on_every_entity(self):
        self.client.force_authenticate(user=self.buyer)
        for entity in sorted(REGISTRY):
            with self.subTest(entity=entity):
                response = self.client.get(self.url(entity, self.listing.pk))
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
                self.assertNotIn('record', response.data)

    def test_importer_is_refused_on_their_own_listing(self):
        """Owning the car is not a reason to see the file kept on it."""
        self.client.force_authenticate(user=self.importer)
        for entity in sorted(REGISTRY):
            with self.subTest(entity=entity):
                response = self.client.get(self.url(entity, self.listing.pk))
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
                self.assertNotIn('record', response.data)

    def test_anonymous_is_refused(self):
        for entity in sorted(REGISTRY):
            with self.subTest(entity=entity):
                response = self.client.get(self.url(entity, self.listing.pk))
                self.assertIn(response.status_code,
                              (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_a_refused_read_is_not_logged_as_a_view(self):
        self.client.force_authenticate(user=self.buyer)
        self.client.get(self.url())
        self.assertFalse(
            AuditLog.objects.filter(action='view', object_id=self.listing.pk).exists()
        )

    def test_unknown_entity_is_404_and_says_what_exists(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(self.url('unicorn', 1))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn('listing', response.data['available'])

    def test_missing_listing_is_404(self):
        response = self.inspect(pk=99_999_999)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_staff_without_the_admin_role_may_read(self):
        """`IsAdminRole` accepts is_staff; the inspector must not diverge."""
        staffer = User.objects.create_user(
            email='inspector-staff@test.sa', password='Passw0rd!x',
            name='Staffer', role='user', is_staff=True,
        )
        self.client.force_authenticate(user=staffer)
        self.assertEqual(self.client.get(self.url()).status_code, status.HTTP_200_OK)


class InspectorLeakTests(InspectorTestBase):
    """The inspector shows more than the public API — and only it does."""

    def _inspector_keys(self):
        response = self.inspect()
        return {
            field['key']
            for section in response.data['record']
            for field in section['fields']
        }

    def test_inspector_returns_fields_the_public_payload_does_not(self):
        self.listing.admin_notes = 'Photos look lifted from a UK auction.'
        self.listing.rejection_reason = 'Previously rejected for the same VIN.'
        self.listing.status_changed_by = self.admin
        self.listing.promotion_expires_at = timezone.now() + timedelta(days=3)
        self.listing.save()

        keys = self._inspector_keys()
        request = RequestFactory().get('/api/listings/')
        request.user = self.buyer
        public = set(ListingSerializer(self.listing, context={'request': request}).data)

        internal = keys - public
        self.assertTrue(internal, 'the inspector adds nothing an admin cannot already see')
        for expected in ('admin_notes', 'status_changed_by', 'promotion_expires_at',
                         'current_reservation'):
            self.assertIn(expected, keys)
            self.assertNotIn(expected, public)

    def test_internal_fields_are_marked_as_internal(self):
        response = self.inspect()
        flags = {
            field['key']: field['internal']
            for section in response.data['record']
            for field in section['fields']
        }
        self.assertTrue(flags['admin_notes'])
        self.assertTrue(flags['rejection_reason'])
        self.assertFalse(flags['make'])

    def test_public_payload_is_byte_for_byte_what_it_was(self):
        """The public serializer gained nothing while the inspector was built."""
        request = RequestFactory().get('/api/listings/')
        request.user = None
        from django.contrib.auth.models import AnonymousUser
        request.user = AnonymousUser()
        actual = set(ListingSerializer(self.listing, context={'request': request}).data)
        self.assertEqual(
            actual - PUBLIC_LISTING_FIELDS, set(),
            'the public listing payload grew — a field meant for the inspector '
            'is now visible to anonymous callers',
        )
        self.assertEqual(
            PUBLIC_LISTING_FIELDS - actual, set(),
            'the public listing payload shrank — check nothing working was removed',
        )

    def test_admin_only_fields_stay_out_of_the_public_payload(self):
        request = RequestFactory().get('/api/listings/')
        from django.contrib.auth.models import AnonymousUser
        request.user = AnonymousUser()
        public = set(ListingSerializer(self.listing, context={'request': request}).data)
        for hidden in ('admin_notes', 'rejection_reason', 'status_changed_by',
                       'current_reservation', 'promotion_expires_at'):
            self.assertNotIn(hidden, public)


class InspectorAuditTests(InspectorTestBase):
    def test_the_trail_holds_only_this_object(self):
        other = Listing.objects.create(
            owner=self.importer, make='BMW', model='M5', year=2023,
            price=390000, city='Jeddah', status='pending',
        )
        AuditLog.objects.create(
            user=self.admin, action='update', model_name='Listing',
            object_id=self.listing.pk, new_value={'price': '420000'},
        )
        AuditLog.objects.create(
            user=self.admin, action='update', model_name='Listing',
            object_id=other.pk, new_value={'price': '390000'},
        )
        AuditLog.objects.create(
            user=self.admin, action='update', model_name='Report',
            object_id=self.listing.pk, new_value={'status': 'resolved'},
        )

        trail = self.inspect().data['audit_trail']
        self.assertTrue(trail)
        for entry in trail:
            self.assertNotEqual(entry['id'], None)
        ids = {entry['id'] for entry in trail}
        self.assertNotIn(
            AuditLog.objects.get(object_id=other.pk).pk, ids,
            'another listing’s history appeared in this one’s trail',
        )
        self.assertNotIn(
            AuditLog.objects.get(model_name='Report').pk, ids,
            'a Report row with the same id leaked into the listing trail',
        )

    def test_lowercased_model_names_are_still_found(self):
        """Half the rows in production say 'listing', not 'Listing'."""
        AuditLog.objects.create(
            user=self.importer, action='create', model_name='listing',
            object_id=self.listing.pk, new_value={'status': 'draft'},
        )
        actions = {entry['action'] for entry in self.inspect().data['audit_trail']}
        self.assertIn('create', actions)

    def test_the_diff_shows_only_what_changed(self):
        AuditLog.objects.create(
            user=self.importer, action='update', model_name='Listing',
            object_id=self.listing.pk,
            old_value={'price': '420000', 'make': 'Audi', 'year': 2024},
            new_value={'price': '395000', 'make': 'Audi', 'year': 2024},
        )
        entry = next(e for e in self.inspect().data['audit_trail']
                     if e['action'] == 'update')
        self.assertEqual(entry['changes'],
                         [{'field': 'price', 'before': '420000', 'after': '395000'}])

    def test_reading_the_record_is_itself_recorded(self):
        self.inspect()
        row = AuditLog.objects.get(action='view', object_id=self.listing.pk)
        self.assertEqual(row.user, self.admin)
        self.assertEqual(row.model_name, 'Listing')

    def test_price_history_comes_out_of_the_log(self):
        AuditLog.objects.create(
            user=self.importer, action='update', model_name='Listing',
            object_id=self.listing.pk,
            old_value={'price': '420000'}, new_value={'price': '250000'},
        )
        history = self.inspect().data['related']['price_history']
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]['before'], '420000')
        self.assertEqual(history[0]['after'], '250000')


class InspectorRiskSignalTests(InspectorTestBase):
    def _codes(self, pk=None):
        return {signal['code'] for signal in self.inspect(pk=pk).data['risk_signals']}

    def test_duplicate_vin_fires(self):
        """
        The unique constraint compares bytes; this compares cars.
        """
        twin = Listing.objects.create(
            owner=self.importer, make='Audi', model='RS6', year=2024,
            price=415000, city='Jeddah', status='approved',
            vin=self.listing.vin.lower(),
        )
        signals = {s['code']: s for s in self.inspect().data['risk_signals']}
        self.assertIn('duplicate_vin', signals)
        self.assertEqual(
            [row['id'] for row in signals['duplicate_vin']['evidence']['listings']],
            [twin.pk],
        )

    def test_duplicate_vin_from_another_importer_is_more_serious(self):
        rival = User.objects.create_user(
            email='rival@test.sa', password='Passw0rd!x', name='Rival',
            role='importer',
        )
        Listing.objects.create(
            owner=rival, make='Audi', model='RS6', year=2024, price=415000,
            city='Jeddah', status='approved', vin=self.listing.vin.lower(),
        )
        signals = {s['code']: s for s in self.inspect().data['risk_signals']}
        self.assertEqual(signals['duplicate_vin']['severity'], 'high')

    def test_no_duplicate_vin_signal_when_the_vin_is_unique(self):
        self.assertNotIn('duplicate_vin', self._codes())

    def test_unverified_importer_fires(self):
        self.profile.cr_verification_status = 'pending_initial'
        self.profile.save(update_fields=['cr_verification_status'])
        self.importer.is_business_verified = False
        self.importer.save(update_fields=['is_business_verified'])
        codes = self._codes()
        self.assertIn('cr_unverified', codes)
        self.assertIn('importer_unverified', codes)

    def test_missing_importer_profile_fires(self):
        self.profile.delete()
        self.assertIn('no_importer_profile', self._codes())

    def test_expired_cr_fires(self):
        self.profile.cr_verification_status = 'expired'
        self.profile.cr_expiry_date = timezone.localdate() - timedelta(days=5)
        self.profile.save(update_fields=['cr_verification_status', 'cr_expiry_date'])
        codes = self._codes()
        self.assertIn('cr_not_valid', codes)
        self.assertIn('cr_expired', codes)

    def test_a_clean_listing_from_a_verified_importer_is_quiet(self):
        """
        No invented alarms.

        Two signals are still true of the fixture and must appear: it has no
        photographs, and the importer's account was made moments ago. Both are
        low — the point is that nothing worse is claimed.
        """
        codes = self._codes()
        self.assertEqual(codes, {'no_images', 'new_account'})
        severities = {s['severity'] for s in self.inspect().data['risk_signals']}
        self.assertEqual(severities, {'low'})

    def test_contact_details_in_the_description_fire(self):
        self.listing.description = 'Call me on 0555123456 to close this today.'
        self.listing.save(update_fields=['description'])
        self.assertIn('contact_in_text', self._codes())

    def test_price_far_off_the_market_fires(self):
        for index in range(4):
            Listing.objects.create(
                owner=self.importer, make='Audi', model='RS6', year=2024,
                price=400000, city='Riyadh', status='approved',
            )
        self.listing.price = 25000
        self.listing.save(update_fields=['price'])
        signals = {s['code']: s for s in self.inspect().data['risk_signals']}
        self.assertIn('price_out_of_range', signals)
        self.assertEqual(signals['price_out_of_range']['evidence']['comparables'], 4)

    def test_price_is_not_judged_without_comparables(self):
        self.listing.price = 25000
        self.listing.save(update_fields=['price'])
        self.assertNotIn('price_out_of_range', self._codes())

    def test_a_suspended_importer_fires(self):
        self.importer.is_suspended = True
        self.importer.suspension_reason = 'Excessive listing creation.'
        self.importer.save(update_fields=['is_suspended', 'suspension_reason'])
        signals = {s['code']: s for s in self.inspect().data['risk_signals']}
        self.assertIn('owner_suspended', signals)
        self.assertEqual(signals['owner_suspended']['severity'], 'high')

    def test_signals_are_ordered_worst_first(self):
        self.profile.delete()
        self.listing.description = 'WhatsApp 0555123456'
        self.listing.save(update_fields=['description'])
        severities = [s['severity'] for s in self.inspect().data['risk_signals']]
        order = {'high': 0, 'medium': 1, 'low': 2}
        self.assertEqual(severities, sorted(severities, key=order.get))


class InspectorActionTests(InspectorTestBase):
    def _actions(self):
        return {a['key']: a for a in self.inspect().data['actions']}

    def test_a_pending_listing_offers_all_three_decisions(self):
        actions = self._actions()
        self.assertTrue(actions['approve']['available'])
        self.assertTrue(actions['reject']['available'])
        self.assertTrue(actions['request_changes']['available'])

    def test_rejection_declares_the_reason_it_needs(self):
        requires = self._actions()['reject']['requires']
        self.assertEqual(requires[0]['field'], 'rejection_reason')
        self.assertTrue(requires[0]['required'])
        self.assertTrue(self._actions()['reject']['destructive'])

    def test_request_changes_declares_its_note(self):
        requires = self._actions()['request_changes']['requires']
        self.assertEqual(requires[0]['field'], 'admin_notes')

    def test_a_sold_listing_blocks_everything_with_a_reason(self):
        Listing.objects.filter(pk=self.listing.pk).update(status='sold')
        for key, offered in self._actions().items():
            with self.subTest(action=key):
                self.assertFalse(offered['available'])
                self.assertIn('sold', offered['reason'])

    def test_an_approved_listing_cannot_be_approved_again(self):
        Listing.objects.filter(pk=self.listing.pk).update(status='approved')
        actions = self._actions()
        self.assertFalse(actions['approve']['available'])
        self.assertEqual(actions['approve']['reason'], 'Already approved.')

    def test_the_endpoints_offered_are_the_endpoints_that_exist(self):
        actions = self._actions()
        self.assertEqual(actions['approve']['endpoint'],
                         f'/api/listings/{self.listing.pk}/approve/')
        self.assertEqual(actions['request_changes']['endpoint'],
                         f'/api/listings/{self.listing.pk}/request-changes/')


class InspectorRecordTests(InspectorTestBase):
    def test_provenance_names_who_did_what(self):
        self.listing.approved_by = self.admin
        self.listing.approved_at = timezone.now()
        self.listing.status_changed_by = self.admin
        self.listing.status_changed_at = timezone.now()
        self.listing.save()
        provenance = self.inspect().data['provenance']
        self.assertEqual(provenance['created_by']['id'], self.importer.pk)
        self.assertEqual(provenance['approved_by']['id'], self.admin.pk)
        self.assertIsNotNone(provenance['time_in_status']['phrase'])

    def test_the_importer_block_carries_their_whole_standing(self):
        importer = self.inspect().data['related']['importer']
        self.assertEqual(importer['email'], self.importer.email)
        self.assertEqual(importer['listing_total'], 1)
        self.assertEqual(importer['listing_counts']['pending'], 1)
        self.assertEqual(importer['profile']['cr_verification_status'], 'verified')

    def test_empty_sections_are_dropped_not_shown_blank(self):
        response = self.inspect()
        for section in response.data['record']:
            self.assertTrue(section['fields'])

    def test_choice_fields_carry_the_code_and_the_label(self):
        self.listing.transmission = 'automatic'
        self.listing.save(update_fields=['transmission'])
        fields = {
            field['key']: field
            for section in self.inspect().data['record']
            for field in section['fields']
        }
        self.assertEqual(fields['transmission']['value'], 'automatic')
        self.assertEqual(fields['transmission']['display'], 'Automatic')
        self.assertEqual(fields['status']['display'], 'Pending')
