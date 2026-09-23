"""
The listing inspector: everything the platform knows about one car.

A reviewer deciding on a pending listing needs three things the public car
page cannot give them — the fields no buyer sees (admin notes, the rejection
that came before, the landed-cost breakdown), who stands behind it (the
importer's registration, their other cars, what happened to them), and the
reasons to hesitate. This assembles all three from what the models already
record; nothing here writes, and nothing here is reachable without the admin
gate in `views.py`.
"""
from decimal import Decimal

from django.db.models import Count, Q
from django.utils import timezone

from cars.models import _LISTING_VALID_TRANSITIONS, Listing
from fraud.models import FraudFlag
from messaging.utils import mask_contact_info
from moderation.models import Report
from orders.models import ImportOrder, Reservation

from .base import (Inspector, action, choice_field, duration_since, field,
                   person, register, risk, section)

#: Report states that still want an admin's attention.
OPEN_REPORT_STATUSES = ('pending', 'investigating')

#: Reservation states that still hold the car.
LIVE_RESERVATION_STATUSES = (
    'pending_payment', 'pending_review', 'active', 'converted_to_order',
)

#: CR states that mean the importer may not be trading right now.
BAD_CR_STATUSES = ('expired', 'suspended')

#: How far a price may sit from comparable approved cars before it is odd.
PRICE_HIGH_MULTIPLE = Decimal('2.5')
PRICE_LOW_MULTIPLE = Decimal('0.4')
#: Below this many comparables the median means nothing, so we say nothing.
PRICE_MIN_COMPARABLES = 3


def _money(value):
    """SAR amounts as the panel shows them; None stays an em dash."""
    if value is None:
        return None
    return f'SAR {value:,.0f}'


def _median(values):
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


@register
class ListingInspector(Inspector):
    entity = 'listing'
    model_name = 'Listing'
    label = 'Listing'

    @classmethod
    def get_object(cls, pk):
        """
        The default manager, deliberately: `Listing.objects.public()` and
        `.moderated()` are the gates for everyone else. An inspector that
        could not open a rejected or soft-deleted car would be useless
        exactly when it is needed.
        """
        return (
            Listing.objects
            .select_related(
                'owner', 'owner__importer_profile', 'approved_by',
                'status_changed_by', 'city_obj', 'source_country_obj',
                'current_reservation',
            )
            .prefetch_related('images')
            .get(pk=pk)
        )

    # -- identity ----------------------------------------------------------
    def headline(self):
        car = self.instance
        name = ' '.join(str(part) for part in (car.year, car.make, car.model) if part)
        return {
            'title': car.title or name,
            'subtitle': name if car.title else None,
            'reference': f'#{car.pk}',
            'image': self._primary_image(),
        }

    def _primary_image(self):
        images = list(self.instance.images.all())
        if not images:
            return None
        primary = next((image for image in images if image.is_primary), images[0])
        return self._image_url(primary)

    def _image_url(self, image):
        try:
            return image.image.url
        except (ValueError, AttributeError):  # no file stored
            return None

    def status(self):
        car = self.instance
        since = car.status_changed_at or car.submitted_at or car.created_at
        return {
            'value': car.status,
            'display': car.get_status_display(),
            'since': duration_since(since),
            'secondary': [
                {'label': 'Import', 'value': car.get_import_status_display()},
                {'label': 'Listed', 'value': 'Active' if car.is_active else 'Hidden'},
                {'label': 'Reserved', 'value': 'Yes' if car.is_reserved else 'No'},
            ],
        }

    def provenance(self):
        car = self.instance
        return {
            'created_at': car.created_at,
            'created_by': person(car.owner),
            'submitted_at': car.submitted_at,
            'approved_at': car.approved_at,
            'approved_by': person(car.approved_by),
            'status_changed_at': car.status_changed_at,
            'status_changed_by': person(car.status_changed_by),
            'time_in_status': duration_since(
                car.status_changed_at or car.submitted_at or car.created_at
            ),
        }

    # -- the record --------------------------------------------------------
    def sections(self):
        car = self.instance
        return [
            self._section_moderation(),
            section('vehicle', 'Vehicle', [
                field('make', 'Make', car.make),
                field('model', 'Model', car.model),
                field('year', 'Year', car.year),
                field('vin', 'VIN', car.vin or None),
                choice_field(car, 'condition', 'Condition'),
                field('mileage', 'Mileage', car.mileage,
                      display=f'{car.mileage:,} km' if car.mileage is not None else None),
                choice_field(car, 'body_type', 'Body type'),
                choice_field(car, 'transmission', 'Transmission'),
                choice_field(car, 'fuel_type', 'Fuel'),
                choice_field(car, 'drive_type', 'Drive'),
                field('engine_size', 'Engine', car.engine_size,
                      display=f'{car.engine_size}L' if car.engine_size else None),
                field('horsepower', 'Horsepower', car.horsepower),
                field('cylinders', 'Cylinders', car.cylinders),
                field('seats', 'Seats', car.seats),
                field('doors', 'Doors', car.doors),
                field('color', 'Exterior colour', car.color or None),
                field('color_interior', 'Interior colour', car.color_interior or None),
            ]),
            section('listing', 'Listing copy', [
                field('title', 'Title', car.title or None),
                field('description', 'Description', car.description or None),
                field('make_ar', 'Make (AR)', car.make_ar or None),
                field('model_ar', 'Model (AR)', car.model_ar or None),
                field('description_ar', 'Description (AR)', car.description_ar or None),
                field('city', 'City', car.city or None),
                field('city_obj', 'City record', car.city_obj_id,
                      display=str(car.city_obj) if car.city_obj else None, internal=True),
                field('coordinates', 'Coordinates',
                      [car.latitude, car.longitude] if car.latitude else None,
                      display=f'{car.latitude}, {car.longitude}' if car.latitude else None),
            ]),
            self._section_pricing(),
            self._section_import(),
            self._section_history(),
            self._section_exposure(),
        ]

    def _section_moderation(self):
        """The fields a buyer never sees — the reason this view exists."""
        car = self.instance
        return section('moderation', 'Moderation', [
            choice_field(car, 'status', 'Status'),
            field('rejection_reason', 'Rejection reason', car.rejection_reason or None,
                  internal=True),
            field('admin_notes', 'Admin notes', car.admin_notes or None, internal=True,
                  hint='Sent to the importer as feedback when changes are requested.'),
            field('status_changed_by', 'Last changed by',
                  car.status_changed_by_id,
                  display=car.status_changed_by.name if car.status_changed_by else None,
                  internal=True),
            field('is_active', 'Active', car.is_active, internal=True,
                  hint='False means soft-deleted: hidden from every list.'),
            field('current_reservation', 'Reservation lock',
                  car.current_reservation_id, internal=True,
                  display=(car.current_reservation.reservation_number
                           if car.current_reservation else None)),
        ], note='Not returned by the public listing API.')

    def _section_pricing(self):
        car = self.instance
        return section('pricing', 'Pricing', [
            field('price', 'Asking price', car.price, display=_money(car.price)),
            field('final_price_sar', 'Final price (SAR)', car.final_price_sar,
                  display=_money(car.final_price_sar)),
            field('negotiable', 'Negotiable', car.negotiable),
            field('source_price', 'Source price', car.source_price,
                  display=(f'{car.source_price:,.0f} {car.source_currency.upper()}'
                           if car.source_price else None), internal=True),
            field('shipping_cost', 'Shipping', car.shipping_cost,
                  display=_money(car.shipping_cost), internal=True),
            field('customs_duty_amount', 'Customs duty', car.customs_duty_amount,
                  display=_money(car.customs_duty_amount), internal=True),
            field('vat_amount', 'VAT', car.vat_amount,
                  display=_money(car.vat_amount), internal=True),
            field('inspection_fee', 'Inspection fee', car.inspection_fee,
                  display=_money(car.inspection_fee), internal=True),
            field('transportation_cost', 'Transport', car.transportation_cost,
                  display=_money(car.transportation_cost), internal=True),
            field('total_landed_cost', 'Total landed cost', car.total_landed_cost,
                  display=_money(car.total_landed_cost), internal=True),
        ])

    def _section_import(self):
        car = self.instance
        return section('import', 'Import & customs', [
            choice_field(car, 'imported_from', 'Imported from'),
            choice_field(car, 'source_country', 'Source country'),
            field('source_city', 'Source city', car.source_city or None),
            choice_field(car, 'auction_source', 'Auction', internal=True),
            field('auction_lot_number', 'Lot number', car.auction_lot_number or None,
                  internal=True),
            field('original_listing_url', 'Original listing',
                  car.original_listing_url or None, internal=True),
            choice_field(car, 'import_status', 'Import status'),
            field('vessel_name', 'Vessel', car.vessel_name or None, internal=True),
            field('shipping_line', 'Shipping line', car.shipping_line or None,
                  internal=True),
            field('bill_of_lading_number', 'Bill of lading',
                  car.bill_of_lading_number or None, internal=True),
            field('container_number', 'Container', car.container_number or None,
                  internal=True),
            field('port_of_origin', 'Port of origin', car.port_of_origin or None),
            choice_field(car, 'port_of_entry', 'Port of entry'),
            field('estimated_arrival_date', 'ETA', car.estimated_arrival_date),
            field('actual_arrival_date', 'Arrived', car.actual_arrival_date),
            field('customs_cleared', 'Customs cleared', car.customs_cleared),
            field('customs_declaration_number', 'Customs declaration',
                  car.customs_declaration_number or None, internal=True),
            field('customs_clearance_date', 'Cleared on', car.customs_clearance_date),
            field('conformity_certificate_number', 'Conformity certificate',
                  car.conformity_certificate_number or None, internal=True),
            choice_field(car, 'vehicle_inspection_result', 'Inspection result'),
            field('vehicle_inspection_date', 'Inspected on', car.vehicle_inspection_date),
            field('gcc_specs', 'GCC specs', car.gcc_specs),
            choice_field(car, 'spec_origin', 'Spec origin'),
            field('odometer_verified', 'Odometer verified', car.odometer_verified),
            field('emissions_compliant', 'Emissions compliant', car.emissions_compliant),
        ])

    def _section_history(self):
        car = self.instance
        return section('history', 'Condition & history', [
            field('accident_history', 'Accident history', car.accident_history),
            field('accident_description', 'Accident description',
                  car.accident_description or None),
            field('has_salvage_title', 'Salvage title', car.has_salvage_title),
            field('has_flood_damage', 'Flood damage', car.has_flood_damage),
            field('has_frame_damage', 'Frame damage', car.has_frame_damage),
            field('damage_description', 'Damage description',
                  car.damage_description or None),
            field('warranty_remaining', 'Warranty remaining', car.warranty_remaining),
            field('service_history', 'Service history', car.service_history),
            field('carfax_url', 'Carfax', car.carfax_url or None, internal=True),
            field('autocheck_url', 'AutoCheck', car.autocheck_url or None, internal=True),
        ])

    def _section_exposure(self):
        car = self.instance
        return section('exposure', 'Exposure', [
            field('view_count', 'Views', car.view_count),
            field('unique_view_count', 'Unique views', car.unique_view_count),
            field('is_featured', 'Featured', car.is_featured),
            field('is_highlighted', 'Highlighted', car.is_highlighted),
            field('is_top_search', 'Top of search', car.is_top_search),
            field('is_homepage', 'On homepage', car.is_homepage),
            field('promotion_priority', 'Promotion priority', car.promotion_priority,
                  internal=True),
            field('promotion_expires_at', 'Promotion expires',
                  car.promotion_expires_at, internal=True),
        ])

    # -- what sits around the record ---------------------------------------
    def related(self):
        return {
            'importer': self._importer(),
            'images': self._images(),
            'reports': self._reports(),
            'reservations': self._reservations(),
            'orders': self._orders(),
            'fraud_flags': self._fraud_flags(),
            'price_history': self._price_history(),
        }

    def _importer(self):
        owner = self.instance.owner
        if owner is None:
            return None
        profile = getattr(owner, 'importer_profile', None)
        counts = dict(
            Listing.objects.filter(owner=owner)
            .values_list('status')
            .annotate(total=Count('id'))
        )
        data = {
            **person(owner),
            'phone': owner.phone,
            'joined': owner.date_joined,
            'account_age': duration_since(owner.date_joined),
            'is_active': owner.is_active,
            'is_suspended': owner.is_suspended,
            'suspension_reason': owner.suspension_reason or None,
            'is_banned': owner.is_banned,
            'warning_count': owner.warning_count,
            'verification_level': owner.verification_level,
            'is_identity_verified': owner.is_identity_verified,
            'is_business_verified': owner.is_business_verified,
            'is_email_verified': owner.is_email_verified,
            'is_phone_verified': owner.is_phone_verified,
            'commercial_registration': owner.commercial_registration or None,
            'commercial_registration_verified': owner.commercial_registration_verified,
            'listing_counts': counts,
            'listing_total': sum(counts.values()),
            'order_count': ImportOrder.objects.filter(importer=owner).count(),
            'complaint_count': Report.objects.filter(reported_user=owner).count(),
            'open_complaint_count': Report.objects.filter(
                reported_user=owner, status__in=OPEN_REPORT_STATUSES).count(),
            'profile': None,
        }
        if profile is not None:
            data['profile'] = {
                'id': profile.pk,
                'business_name': profile.business_name,
                'business_name_ar': profile.business_name_ar or None,
                'commercial_registration': profile.commercial_registration or None,
                'cr_verification_status': profile.cr_verification_status,
                'cr_verification_status_display': profile.get_cr_verification_status_display(),
                'cr_issue_date': profile.cr_issue_date,
                'cr_expiry_date': profile.cr_expiry_date,
                'cr_last_confirmed_date': profile.cr_last_confirmed_date,
                'cr_suspended_at': profile.cr_suspended_at,
                'customs_broker_license': profile.customs_broker_license or None,
                'import_license_number': profile.import_license_number or None,
                'verified_at': profile.verified_at,
                'years_in_business': profile.years_in_business,
                'total_cars_imported': profile.total_cars_imported,
                'average_rating': profile.average_rating,
                'total_reviews': profile.total_reviews,
                'phone': profile.phone or None,
                'email': profile.email or None,
                'city': str(profile.city) if profile.city_id else None,
            }
        return data

    def _images(self):
        return [
            {
                'id': image.pk,
                'url': self._image_url(image),
                'is_primary': image.is_primary,
                'order': image.order,
                'uploaded_at': image.uploaded_at,
            }
            for image in sorted(
                self.instance.images.all(),
                key=lambda image: (not image.is_primary, image.order, image.pk),
            )
        ]

    def _reports(self):
        rows = (
            Report.objects.filter(listing=self.instance)
            .select_related('reporter', 'resolved_by')
            .order_by('-created_at')[:20]
        )
        return [
            {
                'id': row.pk,
                'entity': 'report',
                'reason': row.get_reason_display(),
                'report_type': row.report_type,
                'description': row.description,
                'status': row.status,
                'status_display': row.get_status_display(),
                'priority': row.priority,
                'action_taken': row.action_taken or None,
                'admin_notes': row.admin_notes or None,
                'reporter': person(row.reporter),
                'resolved_by': person(row.resolved_by),
                'resolved_at': row.resolved_at,
                'created_at': row.created_at,
            }
            for row in rows
        ]

    def _reservations(self):
        rows = (
            Reservation.objects.filter(car=self.instance)
            .select_related('buyer', 'importer', 'converted_order')
            .order_by('-created_at')[:20]
        )
        return [
            {
                'id': row.pk,
                'entity': 'reservation',
                'reservation_number': row.reservation_number,
                'status': row.status,
                'status_display': row.get_status_display(),
                'payment_status': row.payment_status,
                'platform_fee_sar': row.platform_fee_sar,
                'paid_at': row.paid_at,
                'buyer': person(row.buyer),
                'cancellation_reason': row.cancellation_reason or None,
                'converted_order': row.converted_order_id,
                'created_at': row.created_at,
            }
            for row in rows
        ]

    def _orders(self):
        rows = (
            ImportOrder.objects.filter(car=self.instance)
            .select_related('buyer', 'importer')
            .order_by('-created_at')[:20]
        )
        return [
            {
                'id': row.pk,
                'entity': 'order',
                'order_number': row.order_number,
                'status': row.status,
                'status_display': row.get_status_display(),
                'total_price': row.total_price,
                'deposit_paid': row.deposit_paid,
                'remaining_balance': row.remaining_balance,
                'buyer': person(row.buyer),
                'created_at': row.created_at,
            }
            for row in rows
        ]

    def _fraud_flags(self):
        rows = (
            FraudFlag.objects.filter(listing=self.instance)
            .select_related('resolved_by')
            .order_by('-created_at')[:20]
        )
        return [
            {
                'id': row.pk,
                'flag_type': row.flag_type,
                'flag_type_display': row.get_flag_type_display(),
                'severity': row.severity,
                'details': row.details,
                'auto_detected': row.auto_detected,
                'is_resolved': row.is_resolved,
                'resolved_by': person(row.resolved_by),
                'resolution_notes': row.resolution_notes or None,
                'created_at': row.created_at,
            }
            for row in rows
        ]

    def _price_history(self):
        """
        Price moves, read back out of the audit log.

        There is no price-history table; the log is the only record that a
        car was listed at one number and quietly changed to another, which is
        exactly what a reviewer wants to see before approving.
        """
        from .audit import audit_trail

        history = []
        for entry in audit_trail(self.model_name, self.instance.pk, limit=500):
            for change in entry['changes']:
                if change['field'] in ('price', 'final_price_sar'):
                    history.append({
                        'field': change['field'],
                        'before': change['before'],
                        'after': change['after'],
                        'actor': entry['actor'],
                        'timestamp': entry['timestamp'],
                    })
        return history

    # -- reasons to hesitate ------------------------------------------------
    def risk_signals(self):
        signals = []
        for check in (
            self._risk_duplicate_vin,
            self._risk_importer,
            self._risk_price,
            self._risk_contact_details,
            self._risk_owner_record,
            self._risk_reports,
            self._risk_fraud_flags,
            self._risk_images,
        ):
            signals.extend(check())
        return signals

    def _risk_duplicate_vin(self):
        """
        Two listings for one chassis.

        `Listing.vin` is unique, but Postgres compares it byte for byte: the
        same VIN in different case, or with stray whitespace, is a different
        row to the database and the same car in the world. That is the
        duplicate this catches — matching the way `fraud.tasks` reasons about
        VINs rather than the way the constraint does.
        """
        vin = (self.instance.vin or '').strip()
        if not vin:
            return []
        others = list(
            Listing.objects.filter(vin__iexact=vin)
            .exclude(pk=self.instance.pk)
            .values('id', 'status', 'owner_id')[:10]
        )
        if not others:
            return []
        same_owner = all(row['owner_id'] == self.instance.owner_id for row in others)
        return [risk(
            'duplicate_vin',
            'medium' if same_owner else 'high',
            'Duplicate VIN',
            (f'{len(others)} other listing(s) carry this VIN'
             + (' — all from this importer.' if same_owner
                else ', at least one from a different importer.')),
            evidence={'vin': vin, 'listings': others},
        )]

    def _risk_importer(self):
        owner = self.instance.owner
        if owner is None:
            return [risk('no_owner', 'high', 'No importer',
                         'This listing has no owner record.')]
        signals = []
        profile = getattr(owner, 'importer_profile', None)
        if profile is None:
            signals.append(risk(
                'no_importer_profile', 'high', 'No importer profile',
                'The owner has no importer profile, so no commercial '
                'registration has ever been checked.',
                evidence={'user_id': owner.pk, 'role': owner.role},
            ))
        else:
            status = profile.cr_verification_status
            if status in BAD_CR_STATUSES:
                signals.append(risk(
                    'cr_not_valid', 'high',
                    f'CR {profile.get_cr_verification_status_display().lower()}',
                    'The importer may not trade while their commercial '
                    'registration is in this state.',
                    evidence={'cr_verification_status': status,
                              'cr_expiry_date': profile.cr_expiry_date},
                ))
            elif status != 'verified':
                signals.append(risk(
                    'cr_unverified', 'medium', 'CR not verified',
                    f'Commercial registration is '
                    f'{profile.get_cr_verification_status_display().lower()}.',
                    evidence={'cr_verification_status': status},
                ))
            expiry = profile.cr_expiry_date
            if expiry and expiry < timezone.localdate():
                signals.append(risk(
                    'cr_expired', 'high', 'CR expired',
                    f'Commercial registration expired on {expiry}.',
                    evidence={'cr_expiry_date': expiry},
                ))
        if not owner.is_business_verified:
            signals.append(risk(
                'importer_unverified', 'medium', 'Importer not verified',
                'The account has not passed business verification.',
                evidence={'verification_level': owner.verification_level,
                          'is_identity_verified': owner.is_identity_verified},
            ))
        return signals

    def _risk_price(self):
        car = self.instance
        asking = car.price or car.final_price_sar
        if asking is None:
            return []
        comparables = list(
            Listing.objects.filter(
                make__iexact=car.make, model__iexact=car.model, year=car.year,
                status='approved',
            )
            .exclude(pk=car.pk)
            .exclude(price__isnull=True)
            .values_list('price', flat=True)[:100]
        )
        if len(comparables) < PRICE_MIN_COMPARABLES:
            return []
        median = _median(comparables)
        if median <= 0:
            return []
        ratio = Decimal(asking) / Decimal(median)
        if ratio >= PRICE_HIGH_MULTIPLE or ratio <= PRICE_LOW_MULTIPLE:
            return [risk(
                'price_out_of_range', 'medium', 'Price out of range',
                f'Asking {_money(asking)} against a median of {_money(median)} '
                f'across {len(comparables)} approved {car.year} {car.make} '
                f'{car.model} listings.',
                evidence={'asking': asking, 'median': median,
                          'ratio': round(float(ratio), 2),
                          'comparables': len(comparables)},
            )]
        return []

    def _risk_contact_details(self):
        car = self.instance
        found = []
        for name, text in (('title', car.title), ('description', car.description),
                           ('description_ar', car.description_ar)):
            if not text:
                continue
            masked, flagged = mask_contact_info(text)
            if flagged:
                found.append({'field': name, 'masked': masked})
        if not found:
            return []
        return [risk(
            'contact_in_text', 'medium', 'Contact details in the listing text',
            'The copy carries a phone number, email or handle — the usual way '
            'a deal is taken off the platform.',
            evidence={'fields': found},
        )]

    def _risk_owner_record(self):
        owner = self.instance.owner
        if owner is None:
            return []
        signals = []
        if owner.is_banned:
            signals.append(risk('owner_banned', 'high', 'Importer is banned',
                                owner.ban_reason or 'The account is banned.'))
        elif owner.is_suspended:
            signals.append(risk(
                'owner_suspended', 'high', 'Importer is suspended',
                owner.suspension_reason or 'The account is suspended.',
                evidence={'suspended_until': owner.suspended_until}))
        cutoff = timezone.now() - timezone.timedelta(days=30)
        rejected = Listing.objects.filter(
            owner=owner, status='rejected', status_changed_at__gte=cutoff,
        ).exclude(pk=self.instance.pk).count()
        if rejected >= 2:
            signals.append(risk(
                'recent_rejections', 'medium', 'Recent rejections',
                f'{rejected} listings from this importer were rejected in the '
                'last 30 days.',
                evidence={'rejected_last_30_days': rejected}))
        age = timezone.now() - owner.date_joined
        if age.days < 7:
            signals.append(risk(
                'new_account', 'low', 'New account',
                f'The importer joined {duration_since(owner.date_joined)["phrase"]} ago.',
                evidence={'date_joined': owner.date_joined}))
        return signals

    def _risk_reports(self):
        open_reports = Report.objects.filter(
            listing=self.instance, status__in=OPEN_REPORT_STATUSES,
        ).count()
        if not open_reports:
            return []
        return [risk(
            'open_reports', 'high' if open_reports > 1 else 'medium',
            'Open reports',
            f'{open_reports} unresolved report(s) name this listing.',
            evidence={'open_reports': open_reports},
        )]

    def _risk_fraud_flags(self):
        rows = FraudFlag.objects.filter(listing=self.instance, is_resolved=False)
        signals = []
        for row in rows:
            signals.append(risk(
                f'fraud_{row.flag_type}',
                'high' if row.severity in ('high', 'critical') else 'medium',
                row.get_flag_type_display(),
                f'Automatic fraud check, severity {row.severity}.'
                if row.auto_detected else
                f'Raised manually, severity {row.severity}.',
                evidence={'flag_id': row.pk, 'details': row.details},
            ))
        return signals

    def _risk_images(self):
        if self.instance.images.all():
            return []
        return [risk('no_images', 'low', 'No photographs',
                     'There is nothing to look at: the listing has no images.')]

    # -- what an admin may do now ------------------------------------------
    def actions(self):
        """
        Approve, reject and request-changes, gated by the model's own
        transition table rather than a second copy of the rules.

        The three endpoints do not check the current status themselves —
        `Listing.clean()` does, and a disallowed jump raises out of the view.
        So an inspector that offered "Approve" on a rejected car would be
        offering a 500. `_LISTING_VALID_TRANSITIONS` is the single source of
        truth for what is legal now.
        """
        car = self.instance
        base = f'/api/listings/{car.pk}/'
        allowed = _LISTING_VALID_TRANSITIONS.get(car.status, set())
        current = car.get_status_display().lower()

        def gate(target):
            if target in allowed:
                return True, None
            if car.status == target:
                return False, f'Already {current}.'
            if car.status == 'sold':
                return False, 'The car is sold — nothing further is allowed.'
            return False, (
                f'A {current} listing cannot move straight to {target.replace("_", " ")}. '
                'The importer has to resubmit it first.'
            )

        approve_ok, approve_why = gate('approved')
        changes_ok, changes_why = gate('changes_requested')
        reject_ok, reject_why = gate('rejected')
        return [
            action(
                'approve', 'Approve', method='PATCH', endpoint=f'{base}approve/',
                available=approve_ok, reason=approve_why,
            ),
            action(
                'request_changes', 'Request changes', method='PATCH',
                endpoint=f'{base}request-changes/',
                available=changes_ok, reason=changes_why,
                requires=[{'field': 'admin_notes', 'label': 'What must change',
                           'type': 'text', 'required': True}],
            ),
            action(
                'reject', 'Reject', method='PATCH', endpoint=f'{base}reject/',
                available=reject_ok, reason=reject_why,
                requires=[{'field': 'rejection_reason', 'label': 'Reason for rejection',
                           'type': 'text', 'required': True}],
                destructive=True,
            ),
        ]
