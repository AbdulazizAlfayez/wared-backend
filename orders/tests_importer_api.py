"""
Importer-side API (feat/importer-api).

1. Reservation payloads carry what the importer app renders without a second
   request, and ?role= splits the two sides of an importer's history.
2. Business verification has one source of truth, and accepting is gated on it.
3. Payouts report confirmed balance payments, read-only.
4. Importer orders say what the next legal status actually is.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from importers.models import ImporterProfile
from payments.models import PaymentTransaction

from .models import ImportOrder, Reservation
from .tests import make_buyer, make_importer, make_listing
from .tests_reservation_flow import confirm_balance, make_reservation

User = get_user_model()


def verify(user, verified=True):
    user.is_business_verified = verified
    user.save(update_fields=['is_business_verified'])
    return user


class ReservationPayloadTests(APITestCase):
    def setUp(self):
        self.importer = verify(make_importer())
        self.buyer = make_buyer(name='Fahad Al Otaibi')
        self.car = make_listing(self.importer, final_price_sar='118000.00')
        self.res = make_reservation(
            self.car, self.buyer, self.importer, buyer_notes='Can you send the Carfax?',
        )
        self.res.activate()

    def _row(self, user, url='/api/reservations/list/'):
        self.client.force_authenticate(user=user)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200, resp.data)
        rows = resp.data['results'] if 'results' in resp.data else resp.data
        return rows[0]

    def test_list_row_has_everything_the_importer_screen_needs(self):
        row = self._row(self.importer)
        self.assertEqual(row['importer_id'], self.importer.pk)
        self.assertEqual(row['buyer'], {
            'id': self.buyer.pk, 'first_name': 'Fahad', 'initials': 'FA',
            # What tapping the row opens — see accounts.BuyerPublicProfileView.
            'profile_url_id': self.buyer.pk,
        })
        self.assertEqual(row['buyer_notes'], 'Can you send the Carfax?')
        self.assertEqual(row['car']['id'], self.car.pk)
        self.assertEqual(row['car']['title'], self.car.title)
        self.assertEqual(row['car']['final_price_sar'], '118000.00')
        self.assertIn('primary_image_url', row['car'])

    def test_hours_remaining_and_time_remaining_hours_agree(self):
        row = self._row(self.importer)
        self.assertEqual(row['hours_remaining'], row['time_remaining_hours'])
        self.assertGreater(row['hours_remaining'], 0)

    def test_nothing_was_renamed(self):
        row = self._row(self.importer)
        for legacy in ('id', 'reservation_number', 'status', 'status_display',
                       'platform_fee_sar', 'payment_method', 'payment_status',
                       'paid_at', 'cancelled_at', 'importer_name', 'expires_at',
                       'hours_remaining', 'can_cancel', 'can_pay',
                       'created_at', 'updated_at'):
            self.assertIn(legacy, row, legacy)
        self.assertIn('primary_image', row['car'])

    def test_detail_still_carries_buyer_notes_and_converted_order(self):
        self.client.force_authenticate(user=self.buyer)
        resp = self.client.get(f'/api/reservations/{self.res.pk}/')
        self.assertEqual(resp.data['buyer_notes'], 'Can you send the Carfax?')
        self.assertIsNone(resp.data['converted_order'])

    def test_pending_for_me_row_matches(self):
        row = self._row(self.importer, '/api/reservations/pending-for-me/')
        self.assertEqual(row['importer_id'], self.importer.pk)
        self.assertEqual(row['buyer']['first_name'], 'Fahad')
        self.assertEqual(row['buyer']['initials'], 'FA')
        self.assertEqual(row['buyer_notes'], 'Can you send the Carfax?')
        self.assertIn('primary_image_url', row['car'])
        self.assertEqual(row['hours_remaining'], row['time_remaining_hours'])
        # Existing keys kept.
        self.assertEqual(row['buyer']['name'], 'Fahad Al Otaibi')
        self.assertIn('member_since', row['buyer'])

    def test_initials_fall_back_to_one_letter(self):
        solo = make_buyer(email='solo@test.com', name='Noura')
        res = make_reservation(make_listing(self.importer), solo, self.importer)
        res.activate()
        self.client.force_authenticate(user=solo)
        rows = self.client.get('/api/reservations/list/').data['results']
        row = next(r for r in rows if r['id'] == res.pk)
        self.assertEqual(row['buyer'], {
            'id': solo.pk, 'first_name': 'Noura', 'initials': 'N',
            'profile_url_id': solo.pk,
        })


class ReservationRoleFilterTests(APITestCase):
    """An importer who also buys gets each side of their history exactly."""

    def setUp(self):
        self.importer = verify(make_importer())
        self.other_importer = make_importer(email='other-imp@test.com', name='Other')
        self.buyer = make_buyer()

        # On the importer's own cars, in every ended state.
        self.as_importer = [
            make_reservation(make_listing(self.importer, title=f'Mine {i}'),
                             self.buyer, self.importer, state)
            for i, state in enumerate(
                ('pending_review', 'converted_to_order', 'cancelled_by_importer', 'expired'))
        ]
        # The same user buying from someone else.
        self.as_buyer = [
            make_reservation(make_listing(self.other_importer, title='Theirs'),
                             self.importer, self.other_importer, 'pending_review'),
        ]

    def _ids(self, query=''):
        self.client.force_authenticate(user=self.importer)
        resp = self.client.get(f'/api/reservations/list/{query}')
        self.assertEqual(resp.status_code, 200, resp.data)
        return {row['id'] for row in resp.data['results']}

    def test_no_filter_shows_both_sides(self):
        self.assertEqual(
            self._ids(),
            {r.pk for r in self.as_importer} | {r.pk for r in self.as_buyer},
        )

    def test_role_importer_is_exactly_their_own_cars(self):
        self.assertEqual(self._ids('?role=importer'), {r.pk for r in self.as_importer})

    def test_role_importer_includes_the_whole_history(self):
        rows = self._ids('?role=importer')
        self.assertEqual(len(rows), 4)  # accepted, rejected, expired, pending

    def test_role_buyer_is_exactly_what_they_placed(self):
        self.assertEqual(self._ids('?role=buyer'), {r.pk for r in self.as_buyer})

    def test_role_and_status_combine(self):
        expired = next(r for r in self.as_importer if r.status == 'expired')
        self.assertEqual(self._ids('?role=importer&status=expired'), {expired.pk})

    def test_unknown_role_is_a_400(self):
        self.client.force_authenticate(user=self.importer)
        resp = self.client.get('/api/reservations/list/?role=staff')
        self.assertEqual(resp.status_code, 400)

    def test_role_importer_for_a_buyer_returns_nothing_of_anyone_elses(self):
        self.client.force_authenticate(user=self.buyer)
        resp = self.client.get('/api/reservations/list/?role=importer')
        self.assertEqual(resp.data['results'], [])


class VerificationSingleTruthTests(APITestCase):
    def setUp(self):
        self.importer = make_importer(is_business_verified=False)
        self.profile, _ = ImporterProfile.objects.get_or_create(
            user=self.importer, defaults={'business_name': 'Imp'},
        )

    def test_profile_is_verified_reads_the_user_flag(self):
        self.assertFalse(self.profile.is_verified)
        verify(self.importer)
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.is_verified)

    def test_public_importer_list_filters_on_the_same_truth(self):
        resp = self.client.get('/api/importers/')
        self.assertNotIn(self.profile.pk, [row['id'] for row in resp.data['results']])
        verify(self.importer)
        resp = self.client.get('/api/importers/')
        self.assertIn(self.profile.pk, [row['id'] for row in resp.data['results']])

    def test_profile_detail_exposes_it(self):
        verify(self.importer)
        resp = self.client.get(f'/api/importers/{self.profile.pk}/')
        self.assertTrue(resp.data['is_verified'])


class AcceptGateTests(APITestCase):
    def setUp(self):
        # Explicitly unverified — make_importer() is verified by default.
        self.importer = make_importer(is_business_verified=False)
        self.buyer = make_buyer()
        self.car = make_listing(self.importer)
        self.res = make_reservation(self.car, self.buyer, self.importer)
        self.res.activate()

    def _accept(self, user):
        self.client.force_authenticate(user=user)
        return self.client.post(f'/api/reservations/{self.res.pk}/accept/')

    def test_unverified_importer_gets_403_with_a_code(self):
        resp = self._accept(self.importer)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(resp.data['code'], 'importer_not_verified')
        self.assertTrue(resp.data['detail'])
        self.assertTrue(resp.data['detail_ar'])
        self.res.refresh_from_db()
        self.assertEqual(self.res.status, 'pending_review')
        self.assertFalse(ImportOrder.objects.filter(car=self.car).exists())

    def test_verified_importer_can_accept(self):
        verify(self.importer)
        resp = self._accept(self.importer)
        self.assertEqual(resp.status_code, 200, resp.data)
        self.res.refresh_from_db()
        self.assertEqual(self.res.status, 'converted_to_order')

    def test_reject_stays_open_to_an_unverified_importer(self):
        self.client.force_authenticate(user=self.importer)
        resp = self.client.post(f'/api/reservations/{self.res.pk}/reject/')
        self.assertEqual(resp.status_code, 200, resp.data)
        self.res.refresh_from_db()
        self.assertEqual(self.res.status, 'cancelled_by_importer')

    def test_staff_can_accept_on_an_unverified_importers_behalf(self):
        staff = make_buyer(email='staff@test.com', name='Staff', is_staff=True)
        resp = self._accept(staff)
        self.assertEqual(resp.status_code, 200, resp.data)


class VerificationChangesRequestedTests(APITestCase):
    def setUp(self):
        from accounts.models import VerificationRequest
        self.user = make_importer(is_business_verified=False)
        self.admin = make_buyer(email='admin@test.com', name='Admin', role='admin')
        self.req = VerificationRequest.objects.create(
            user=self.user, verification_type='commercial_registration',
            document_number='1010101010',
        )

    def _action(self, payload):
        self.client.force_authenticate(user=self.admin)
        return self.client.patch(f'/api/admin/verifications/{self.req.pk}/', payload, format='json')

    def test_admin_can_request_changes_with_a_note(self):
        resp = self._action({'action': 'request_changes', 'admin_note': 'CR scan is unreadable.'})
        self.assertEqual(resp.status_code, 200, resp.data)
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, 'changes_requested')
        self.assertEqual(self.req.admin_note, 'CR scan is unreadable.')
        self.assertFalse(self.user.is_business_verified)

    def test_the_note_is_required(self):
        resp = self._action({'action': 'request_changes'})
        self.assertEqual(resp.status_code, 400)
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, 'pending')

    def test_status_endpoint_shows_it(self):
        self._action({'action': 'request_changes', 'admin_note': 'Send the current CR.'})
        self.client.force_authenticate(user=self.user)
        resp = self.client.get('/api/verification/status/')
        self.assertEqual(resp.data['pending_requests'], [])
        row = resp.data['changes_requested'][0]
        self.assertEqual(row['status'], 'changes_requested')
        self.assertEqual(row['admin_note'], 'Send the current CR.')
        self.assertFalse(resp.data['is_business_verified'])

    def test_a_changes_requested_row_can_still_be_approved(self):
        self._action({'action': 'request_changes', 'admin_note': 'Blurry.'})
        resp = self._action({'action': 'approve'})
        self.assertEqual(resp.status_code, 200, resp.data)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_business_verified)


@override_settings(PLATFORM_COMMISSION_PCT=1.0)
class PayoutsTests(APITestCase):
    def setUp(self):
        self.importer = verify(make_importer())
        self.buyer = make_buyer(name='Fahad Al Otaibi')
        self.car = make_listing(self.importer, title='2022 Toyota Camry TRD')
        self.order = ImportOrder.objects.create(
            car=self.car, buyer=self.buyer, importer=self.importer,
            total_price=Decimal('118000.00'), remaining_balance=0, status='confirmed',
        )

    def _get(self, user=None):
        self.client.force_authenticate(user=user or self.importer)
        return self.client.get('/api/importers/me/payouts/')

    def test_empty_until_a_balance_payment_succeeds(self):
        PaymentTransaction.objects.create(
            order=self.order, user=self.buyer, amount=Decimal('118000.00'),
            payment_type='balance', method='bank_transfer', status='pending',
        )
        resp = self._get()
        self.assertEqual(resp.data['count'], 0)
        self.assertEqual(resp.data['totals'], {'gross': 0.0, 'commission': 0.0, 'net': 0.0})

    def test_one_row_per_confirmed_balance_payment(self):
        confirm_balance(self.order, '118000.00')
        resp = self._get()
        self.assertEqual(resp.data['count'], 1)
        row = resp.data['results'][0]
        self.assertEqual(row['order_id'], self.order.pk)
        self.assertEqual(row['order_number'], self.order.order_number)
        self.assertEqual(row['car_title'], '2022 Toyota Camry TRD')
        self.assertEqual(row['buyer_first_name'], 'Fahad')
        self.assertEqual(row['gross_sar'], 118000.0)
        self.assertEqual(row['commission_pct'], 1.0)
        self.assertEqual(row['commission_sar'], 1180.0)
        self.assertEqual(row['net_sar'], 116820.0)
        self.assertTrue(row['confirmed_at'])
        self.assertEqual(resp.data['totals'],
                         {'gross': 118000.0, 'commission': 1180.0, 'net': 116820.0})

    def test_totals_add_up_over_several_orders(self):
        confirm_balance(self.order, '118000.00')
        second = ImportOrder.objects.create(
            car=make_listing(self.importer, title='Second'), buyer=self.buyer,
            importer=self.importer, total_price=Decimal('50000.00'),
            remaining_balance=0, status='completed',
        )
        confirm_balance(second, '50000.00')
        totals = self._get().data['totals']
        self.assertEqual(totals, {'gross': 168000.0, 'commission': 1680.0, 'net': 166320.0})

    def test_another_importers_payments_are_invisible(self):
        other = verify(make_importer(email='other-imp@test.com', name='Other'))
        other_order = ImportOrder.objects.create(
            car=make_listing(other), buyer=self.buyer, importer=other,
            total_price=Decimal('9000.00'), remaining_balance=0, status='confirmed',
        )
        confirm_balance(other_order, '9000.00')
        confirm_balance(self.order, '118000.00')
        rows = self._get().data['results']
        self.assertEqual([row['order_id'] for row in rows], [self.order.pk])

    def test_deposits_and_promotions_are_not_payouts(self):
        res = make_reservation(self.car, self.buyer, self.importer)
        PaymentTransaction.objects.create(
            reservation=res, user=self.buyer, amount=Decimal('99.00'),
            payment_type='deposit', method='mada', status='succeeded',
        )
        self.assertEqual(self._get().data['count'], 0)

    def test_buyers_cannot_read_payouts(self):
        self.assertEqual(self._get(self.buyer).status_code, status.HTTP_403_FORBIDDEN)

    def test_read_only(self):
        self.client.force_authenticate(user=self.importer)
        resp = self.client.post('/api/importers/me/payouts/', {}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    @override_settings(PLATFORM_COMMISSION_PCT=2.5)
    def test_commission_follows_the_setting_and_rounds_to_halalas(self):
        confirm_balance(self.order, '118000.33')
        row = self._get().data['results'][0]
        self.assertEqual(row['commission_pct'], 2.5)
        self.assertEqual(row['commission_sar'], 2950.01)
        self.assertEqual(row['net_sar'], 115050.32)


class ImporterOrdersPayloadTests(APITestCase):
    def setUp(self):
        self.importer = verify(make_importer())
        self.buyer = make_buyer()
        self.order = ImportOrder.objects.create(
            car=make_listing(self.importer), buyer=self.buyer, importer=self.importer,
            total_price=Decimal('118000.00'), remaining_balance=Decimal('118000.00'),
            status='sourcing',
        )

    def _row(self):
        self.client.force_authenticate(user=self.importer)
        resp = self.client.get('/api/dashboard/importer/orders/')
        self.assertEqual(resp.status_code, 200, resp.data)
        return next(row for row in resp.data if row['id'] == self.order.pk)

    def test_row_carries_the_payment_fields(self):
        row = self._row()
        self.assertEqual(row['payment_status'], 'none')
        self.assertEqual(row['balance_due_sar'], 118000.0)
        self.assertFalse(row['can_exchange_contacts'])

    def test_allowed_next_statuses_hides_the_payment_gated_move(self):
        # 'sourcing' → 'purchased' needs the balance; 'cancelled' does not.
        row = self._row()
        self.assertEqual(row['allowed_next_statuses'], ['cancelled'])
        self.assertIn('purchased', self.order.allowed_next_statuses())

    def test_the_gated_move_appears_once_the_balance_is_confirmed(self):
        confirm_balance(self.order, '118000.00')
        row = self._row()
        self.assertEqual(sorted(row['allowed_next_statuses']), ['cancelled', 'purchased'])
        self.assertEqual(row['payment_status'], 'paid')
        self.assertTrue(row['can_exchange_contacts'])

    def test_what_it_offers_is_what_update_status_accepts(self):
        confirm_balance(self.order, '118000.00')
        for next_status in self._row()['allowed_next_statuses']:
            order = ImportOrder.objects.create(
                car=make_listing(self.importer, title=f'car {next_status}'),
                buyer=self.buyer, importer=self.importer,
                total_price=Decimal('1.00'), remaining_balance=0, status='sourcing',
            )
            confirm_balance(order, '1.00')
            self.client.force_authenticate(user=self.importer)
            resp = self.client.patch(
                f'/api/orders/{order.pk}/update-status/',
                {'status': next_status}, format='json',
            )
            self.assertEqual(resp.status_code, 200, (next_status, resp.data))

    def test_a_terminal_order_offers_nothing(self):
        self.order.status = 'cancelled'
        self.order.save(update_fields=['status'])
        self.assertEqual(self._row()['allowed_next_statuses'], [])
