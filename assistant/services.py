"""
WARED AI Assistant — service layer.

Calls the Anthropic API with tool-use to answer questions about
available cars and the user's orders. Returns both text and structured
card data for the frontend to render visually.
"""
import json
import logging
import re

from django.conf import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------

class AssistantUnavailable(Exception):
    pass


# ---------------------------------------------------------------------------
# Anthropic client
# ---------------------------------------------------------------------------

def get_anthropic_client():
    import anthropic
    key = settings.ANTHROPIC_API_KEY
    if not key:
        raise AssistantUnavailable('ANTHROPIC_API_KEY is not configured.')
    return anthropic.Anthropic(api_key=key, timeout=45.0)


# ---------------------------------------------------------------------------
# Image helper — Cloudinary ~480px thumbnail
# ---------------------------------------------------------------------------

def _listing_thumbnail(listing):
    """Return a ~480px-wide Cloudinary thumbnail URL for the listing's
    primary image, or None if the listing has no images."""
    try:
        primary = listing.images.filter(is_primary=True).first() or listing.images.first()
        if primary and primary.image:
            url = primary.image.url
            # Cloudinary transform: insert w_480,c_fill,q_auto before the
            # version segment (e.g. /upload/v1234/ → /upload/w_480,c_fill,q_auto/v1234/)
            if 'cloudinary' in url and '/upload/' in url:
                url = url.replace('/upload/', '/upload/w_480,c_fill,q_auto/', 1)
            return url
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are WARED Assistant, the helpful support assistant for WARED (وارد), \
a Saudi marketplace for imported cars. Always reply in the same language \
the user wrote in (Arabic or English). Keep answers short, warm, and clear. \
Use simple words.

PLATFORM FAQ
- Only verified importers can list cars on WARED. Regular users (buyers) \
  browse, compare, favorite, and search.
- Cars are imported from abroad (USA, Japan, Korea, UAE, Europe, etc.).
- Buying flow: reserve the car ({deposit_sar} SAR platform fee) → the importer \
  accepts → an order is created and the car is held for you → you pay the \
  remaining balance by bank transfer to WARED (bank details are shown ONLY \
  on your order page) → WARED admin confirms the payment → the importer \
  handles sourcing/shipping/customs → delivery. Every step appears in your \
  order timeline.
- Payment states a buyer may see: awaiting payment, under review (after \
  submitting transfer reference), paid.
- Chat between buyer and importer hides phone numbers/emails/links until \
  payment is confirmed — this protects both sides.  Deals should stay \
  on-platform.
- Cancellations/refunds: the buyer can cancel from the order page; refunds \
  are handled by WARED admin.
- For unresolved problems or disputes, tell the user to contact WARED \
  support from the app or their order page.

HARD RULES — YOU MUST FOLLOW THESE:
- NEVER state bank account details, IBANs, or beneficiary names, even if \
  asked, even if a tool returns them. Always say: "You'll find the official \
  payment details on your order page — never trust bank details sent any \
  other way."
- NEVER reveal information about other users, other people's orders, or \
  contact details (phones, emails, social handles) of anyone.
- Use tools for any question about the user's orders or about available \
  cars. NEVER invent order numbers, statuses, dates, prices, or cars.  \
  If a tool returns nothing, say you couldn't find it.
- If the user is not logged in and asks about their orders, politely ask \
  them to log in first.
- Only discuss WARED and car-buying on WARED. Politely decline anything \
  else (coding help, world news, other websites, legal/financial advice \
  beyond explaining how WARED works).
- NEVER claim escrow or guarantees. NEVER discuss WARED's internal \
  commission, importer payouts, or admin operations.
- NEVER promise a refund or a timeline for one.
- If the user seems angry or reports fraud, be empathetic and direct them \
  to WARED support.

VISUAL CARDS RULE:
When you use search_cars or the order tools, the app automatically shows \
the results as visual cards under your message — with photo, price/status, \
and a link. Therefore DO NOT list the cars' or orders' details in your text. \
Write only 1–2 short sentences: a helpful observation or recommendation \
(for example which car fits their budget best and why) plus one follow-up \
question. Never repeat prices, years, or statuses the cards already show.
""".format(deposit_sar=getattr(settings, 'PLATFORM_DEPOSIT_AMOUNT_SAR', 99))


# ---------------------------------------------------------------------------
# Tool definitions (Anthropic format)
# ---------------------------------------------------------------------------

TOOL_SEARCH_CARS = {
    'name': 'search_cars',
    'description': (
        'Search available cars on the WARED marketplace. '
        'Use this when the user asks about cars, prices, or availability.'
    ),
    'input_schema': {
        'type': 'object',
        'properties': {
            'make':           {'type': 'string', 'description': 'Car manufacturer, e.g. Toyota'},
            'model':          {'type': 'string', 'description': 'Car model, e.g. Camry'},
            'year_min':       {'type': 'integer', 'description': 'Minimum year'},
            'year_max':       {'type': 'integer', 'description': 'Maximum year'},
            'price_min':      {'type': 'number',  'description': 'Minimum price in SAR'},
            'price_max':      {'type': 'number',  'description': 'Maximum price in SAR'},
            'source_country': {'type': 'string',  'description': 'Import source, e.g. american, japanese'},
            'body_type':      {'type': 'string',  'description': 'Body type, e.g. sedan, suv'},
            'limit':          {'type': 'integer', 'description': 'Max results (default 5, max 8)'},
        },
        'required': [],
    },
}

TOOL_GET_MY_ORDERS = {
    'name': 'get_my_orders',
    'description': (
        'Get the current user\'s orders (as buyer or importer). '
        'Use when the user asks about their orders, deliveries, or purchases.'
    ),
    'input_schema': {
        'type': 'object',
        'properties': {},
        'required': [],
    },
}

TOOL_GET_ORDER_DETAILS = {
    'name': 'get_order_details',
    'description': (
        'Get detailed information about a specific order including its '
        'timeline. Use when the user asks about a specific order.'
    ),
    'input_schema': {
        'type': 'object',
        'properties': {
            'order_id': {'type': 'integer', 'description': 'The order ID'},
        },
        'required': ['order_id'],
    },
}


# ---------------------------------------------------------------------------
# Tool executors
# ---------------------------------------------------------------------------

def _execute_search_cars(params, user):
    from cars.models import Listing
    from cars.visibility import public_market_q

    qs = Listing.objects.filter(public_market_q()).prefetch_related('images').distinct()

    if v := params.get('make'):
        qs = qs.filter(make__icontains=v)
    if v := params.get('model'):
        qs = qs.filter(model__icontains=v)
    if v := params.get('year_min'):
        qs = qs.filter(year__gte=v)
    if v := params.get('year_max'):
        qs = qs.filter(year__lte=v)
    if v := params.get('price_min'):
        qs = qs.filter(price__gte=v)
    if v := params.get('price_max'):
        qs = qs.filter(price__lte=v)
    if v := params.get('source_country'):
        qs = qs.filter(imported_from__icontains=v)
    if v := params.get('body_type'):
        qs = qs.filter(body_type__icontains=v)

    total = qs.count()
    limit = min(params.get('limit', 5), 8)
    cars = []
    for c in qs.order_by('-created_at')[:limit]:
        city_name = ''
        try:
            city_name = c.city_obj.name_en if c.city_obj else (c.city or '')
        except Exception:
            city_name = c.city or ''
        cars.append({
            'id': c.id,
            'make': c.make,
            'model': c.model,
            'year': c.year,
            'price': f"{c.final_price_sar or c.price} SAR",
            'source_country': c.get_imported_from_display() if c.imported_from else '',
            'city': city_name,
            'image': _listing_thumbnail(c),
            'url': f"/car/{c.id}",
        })
    return {'cars': cars, 'total_matches': total}


def _execute_get_my_orders(params, user):
    if not user or not user.is_authenticated:
        return {'error': 'not authenticated'}

    from django.db.models import Q
    from orders.models import ImportOrder

    qs = ImportOrder.objects.filter(
        Q(buyer=user) | Q(importer=user)
    ).select_related('car').prefetch_related('car__images').order_by('-created_at')[:10]

    orders = []
    for o in qs:
        your_role = 'importer' if o.importer_id == user.pk else 'buyer'
        orders.append({
            'id': o.id,
            'order_number': o.order_number,
            'car': f"{o.car.year} {o.car.make} {o.car.model}",
            'status': o.get_status_display(),
            'payment_status': _get_payment_status_label(o),
            'created_at': o.created_at.strftime('%Y-%m-%d'),
            'estimated_delivery_date': str(o.estimated_delivery_date) if o.estimated_delivery_date else None,
            'your_role': your_role,
            'image': _listing_thumbnail(o.car),
            'url': f"/orders/{o.id}",
        })
    return {'orders': orders}


def _execute_get_order_details(params, user):
    if not user or not user.is_authenticated:
        return {'error': 'not authenticated'}

    from orders.models import ImportOrder

    try:
        order = ImportOrder.objects.select_related('car').prefetch_related('car__images').get(pk=params['order_id'])
    except ImportOrder.DoesNotExist:
        return {'error': 'not found'}

    # Ownership check: only the buyer or importer may see details
    if order.buyer_id != user.pk and order.importer_id != user.pk:
        return {'error': 'not found'}

    # Timeline events (public ones only)
    timeline = []
    for ev in order.timeline_events.filter(is_public=True).order_by('-date')[:15]:
        timeline.append({
            'date': ev.date.strftime('%Y-%m-%d'),
            'title': ev.title,
        })

    remaining = float(order.remaining_balance) if order.remaining_balance else None

    return {
        'id': order.id,
        'order_number': order.order_number,
        'car': f"{order.car.year} {order.car.make} {order.car.model}",
        'status': order.get_status_display(),
        'payment_status': _get_payment_status_label(order),
        'remaining_balance_sar': remaining,
        'delivery_method': order.get_delivery_method_display(),
        'estimated_delivery_date': str(order.estimated_delivery_date) if order.estimated_delivery_date else None,
        'timeline': timeline,
        'image': _listing_thumbnail(order.car),
        'url': f"/orders/{order.id}",
    }


def _get_payment_status_label(order):
    """Mirror the serializer logic for payment_status."""
    if order.status == 'refunded':
        return 'refunded'
    try:
        from payments.models import PaymentTransaction
        balance = PaymentTransaction.objects.filter(
            order=order, payment_type='balance',
        ).order_by('-created_at').first()
        if balance:
            if balance.status == 'succeeded':
                return 'paid'
            if balance.status == 'pending':
                return 'under_review'
    except Exception:
        pass
    if order.deposit_paid:
        return 'deposit_paid'
    return 'awaiting_payment'


TOOL_EXECUTORS = {
    'search_cars':       _execute_search_cars,
    'get_my_orders':     _execute_get_my_orders,
    'get_order_details': _execute_get_order_details,
}


# ---------------------------------------------------------------------------
# Main chat function
# ---------------------------------------------------------------------------

def _empty_cards():
    return {'cars': [], 'cars_meta': None, 'orders': []}


def run_chat(user, history, new_message):
    """
    Run one chat turn. Returns (reply_text, cards).
    cards = {"cars": [...], "cars_meta": {...}|null, "orders": [...]}.
    Raises AssistantUnavailable on API errors.
    """
    import anthropic

    client = get_anthropic_client()

    # Build system prompt with user context
    if user and user.is_authenticated:
        display_name = getattr(user, 'name', '') or 'a user'
        role = getattr(user, 'role', 'user')
        user_line = f"The user is logged in as {display_name} with role {role}."
    else:
        user_line = "The user is not logged in."

    system = f"{user_line}\n\n{SYSTEM_PROMPT}"

    # Build messages
    messages = []
    limit = settings.ASSISTANT_HISTORY_LIMIT
    for item in history[-limit:]:
        messages.append({'role': item['role'], 'content': item['content']})
    messages.append({'role': 'user', 'content': new_message})

    # Build tools list
    tools = [TOOL_SEARCH_CARS]
    is_authenticated = user and user.is_authenticated
    if is_authenticated:
        tools.extend([TOOL_GET_MY_ORDERS, TOOL_GET_ORDER_DETAILS])

    try:
        response = client.messages.create(
            model=settings.ASSISTANT_MODEL,
            max_tokens=settings.ASSISTANT_MAX_TOKENS,
            system=system,
            messages=messages,
            tools=tools,
        )
    except (anthropic.APIError, anthropic.APITimeoutError) as exc:
        exc_name = type(exc).__name__
        exc_msg = str(exc)
        logger.warning('Anthropic API error: %s — %s', exc_name, exc_msg)
        if 'credit' in exc_msg.lower() or 'billing' in exc_msg.lower() or exc_name == 'AuthenticationError':
            logger.warning('Hint: check billing/key at console.anthropic.com')
        raise AssistantUnavailable(exc_msg)

    # Collect structured card data from tool results
    collected_cars = []     # deduped by id
    collected_orders = []   # deduped by id
    cars_meta = None
    seen_car_ids = set()
    seen_order_ids = set()

    # Tool-use loop
    rounds = 0
    while response.stop_reason == 'tool_use' and rounds < settings.ASSISTANT_MAX_TOOL_ROUNDS:
        rounds += 1
        messages.append({'role': 'assistant', 'content': response.content})

        tool_results = []
        for block in response.content:
            if block.type == 'tool_use':
                executor = TOOL_EXECUTORS.get(block.name)
                if executor:
                    result = executor(block.input, user)
                else:
                    result = {'error': f'Unknown tool: {block.name}'}

                # Capture structured data
                if block.name == 'search_cars' and 'cars' in result:
                    for car in result['cars']:
                        cid = car.get('id')
                        if cid and cid not in seen_car_ids:
                            seen_car_ids.add(cid)
                            collected_cars.append(car)
                    cars_meta = {
                        'total_matches': result.get('total_matches', 0),
                        'params': block.input,
                    }
                elif block.name == 'get_my_orders' and 'orders' in result:
                    for order in result['orders']:
                        oid = order.get('id')
                        if oid and oid not in seen_order_ids:
                            seen_order_ids.add(oid)
                            collected_orders.append(order)
                elif block.name == 'get_order_details' and 'order_number' in result:
                    oid = result.get('id')
                    if oid and oid not in seen_order_ids:
                        seen_order_ids.add(oid)
                        collected_orders.append({
                            'id': oid,
                            'order_number': result['order_number'],
                            'car': result['car'],
                            'image': result.get('image'),
                            'status': result['status'],
                            'payment_status': result['payment_status'],
                            'url': result.get('url', f"/orders/{oid}"),
                        })

                tool_results.append({
                    'type': 'tool_result',
                    'tool_use_id': block.id,
                    'content': json.dumps(result, ensure_ascii=False, default=str),
                })

        messages.append({'role': 'user', 'content': tool_results})

        try:
            response = client.messages.create(
                model=settings.ASSISTANT_MODEL,
                max_tokens=settings.ASSISTANT_MAX_TOKENS,
                system=system,
                messages=messages,
                tools=tools,
            )
        except (anthropic.APIError, anthropic.APITimeoutError) as exc:
            exc_name = type(exc).__name__
            exc_msg = str(exc)
            logger.warning('Anthropic API error in tool loop: %s — %s', exc_name, exc_msg)
            if 'credit' in exc_msg.lower() or 'billing' in exc_msg.lower() or exc_name == 'AuthenticationError':
                logger.warning('Hint: check billing/key at console.anthropic.com')
            raise AssistantUnavailable(exc_msg)

    # Extract final text
    reply_parts = []
    for block in response.content:
        if block.type == 'text':
            reply_parts.append(block.text)
    reply_text = '\n'.join(reply_parts) or "I'm sorry, I couldn't generate a response."

    # Build cards — cap and strip orders for anon
    cards = {
        'cars': collected_cars[:6],
        'cars_meta': cars_meta,
        'orders': collected_orders[:3] if is_authenticated else [],
    }

    return reply_text, cards
