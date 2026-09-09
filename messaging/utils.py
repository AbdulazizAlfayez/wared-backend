"""
Contact-masking utilities for WARED messaging.

HONESTY NOTE: This masking is best-effort friction to discourage sharing contact
details before the deposit model secures the platform's commission. It is NOT a
security guarantee. Determined users can circumvent any text-based filter.
Enforcement ultimately relies on the deposit/commission model and the Terms of
Service, not on perfect pattern matching.

Public API:
    mask_contact_info(text) -> (masked_text, was_flagged)
    mask_conversation_messages(messages, sender_id) -> list[masked_text]
    get_allow_contact(conversation) -> bool
"""

import re
from typing import Optional

_PLACEHOLDER = '[ تم إخفاء جهة الاتصال لحمايتك / hidden by WARED ]'

# ═══════════════════════════════════════════════════════════════════════════════
# 1. Number-word normalisation (English + Arabic → digits)
# ═══════════════════════════════════════════════════════════════════════════════

# English digit words including common misspellings / substitutions
_EN_WORDS = {
    'zero': '0', 'oh': '0', 'o': '0',
    'one': '1', 'won': '1',
    'two': '2', 'too': '2', 'to': '2',
    'three': '3', 'thre': '3', 'tree': '3',
    'four': '4', 'for': '4', 'fore': '4',
    'five': '5', 'fiv': '5',
    'six': '6', 'siz': '6',
    'seven': '7', 'sevn': '7', 'sven': '7',
    'eight': '8', 'eit': '8',
    'nine': '9', 'nne': '9', 'nein': '9',
}

# Arabic digit words
_AR_WORDS = {
    'صفر': '0',
    'واحد': '1',
    'اثنين': '2', 'اثنان': '2', 'ثنين': '2',
    'ثلاثة': '3', 'ثلاث': '3', 'تلاته': '3', 'ثلاثه': '3',
    'أربعة': '4', 'اربعة': '4', 'اربع': '4', 'أربع': '4', 'اربعه': '4',
    'خمسة': '5', 'خمس': '5', 'خمسه': '5',
    'ستة': '6', 'ست': '6', 'سته': '6',
    'سبعة': '7', 'سبع': '7', 'سبعه': '7',
    'ثمانية': '8', 'ثمان': '8', 'تمانيه': '8', 'ثمانيه': '8',
    'تسعة': '9', 'تسع': '9', 'تسعه': '9',
}

_ALL_DIGIT_WORDS = {**_EN_WORDS, **_AR_WORDS}

# Build a regex that matches any digit word (longest first to avoid partial matches)
_DIGIT_WORD_RE = re.compile(
    r'\b(' + '|'.join(
        re.escape(w) for w in sorted(_ALL_DIGIT_WORDS.keys(), key=len, reverse=True)
    ) + r')\b',
    re.IGNORECASE | re.UNICODE,
)

# Arabic-Indic numerals (٠١٢٣٤٥٦٧٨٩) → Western
_ARABIC_INDIC_MAP = str.maketrans('٠١٢٣٤٥٦٧٨٩', '0123456789')


def _normalise_to_digits(text: str) -> str:
    """Convert spelled-out digits and Arabic-Indic numerals to Western digits."""
    # Arabic-Indic numerals
    result = text.translate(_ARABIC_INDIC_MAP)
    # Spelled-out words
    result = _DIGIT_WORD_RE.sub(
        lambda m: _ALL_DIGIT_WORDS.get(m.group(1).lower(), m.group(0)),
        result,
    )
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Price / year false-positive protection
# ═══════════════════════════════════════════════════════════════════════════════

# Numbers that look like prices (preceded/followed by currency indicators)
_PRICE_CONTEXT_RE = re.compile(
    r'(?:SAR|sar|ر\.?\s*س|ريال|SR|﷼)\s*[\d,.]+'
    r'|'
    r'[\d,.]+\s*(?:SAR|sar|ر\.?\s*س|ريال|SR|﷼)',
    re.UNICODE,
)

# Model years: 4-digit numbers between 1990-2039
_YEAR_RE = re.compile(r'\b(19[9]\d|20[0-3]\d)\b')


def _protect_prices_and_years(text: str) -> tuple[str, dict[str, str]]:
    """Replace prices and years with safe tokens so they don't get masked."""
    protected = {}
    counter = [0]

    def _replace(match: re.Match) -> str:
        token = f'__PROTECTED_{counter[0]}__'
        protected[token] = match.group(0)
        counter[0] += 1
        return token

    result = _PRICE_CONTEXT_RE.sub(_replace, text)
    result = _YEAR_RE.sub(_replace, result)
    return result, protected


def _restore_protected(text: str, protected: dict[str, str]) -> str:
    """Restore price/year tokens back to original text."""
    for token, original in protected.items():
        text = text.replace(token, original)
    return text


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Core patterns
# ═══════════════════════════════════════════════════════════════════════════════

# Phone numbers: +966, 00966, 05xxxxxxxx, or any run of 7+ digits
_PHONE_RE = re.compile(
    r'(?:\+966|00966|0[5]\d)'
    r'[\d\s\-\.]{6,12}'
    r'|'
    r'\d[\d\s\-\.]{6,}\d',
    re.UNICODE,
)

# Email addresses
_EMAIL_RE = re.compile(
    r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z]{2,}',
    re.UNICODE,
)

# URLs / domains
_URL_RE = re.compile(
    r'https?://\S+'
    r'|'
    r'www\.\S+'
    r'|'
    r'(?:wa\.me|t\.me|instagram\.com|snapchat\.com|tiktok\.com)/\S*'
    r'|'
    r'\S+\.(?:com|sa|net|org|io|me|co)\b',
    re.IGNORECASE | re.UNICODE,
)

# Social handles: @handle (at least 2 chars after @)
_HANDLE_RE = re.compile(r'@[a-zA-Z0-9_]{2,}', re.UNICODE)

# Off-platform keywords (English + Arabic)
_KEYWORDS_RE = re.compile(
    r'|'.join([
        r'whatsapp', r'whats\s*app', r'واتساب', r'واتس',
        r'اتصل', r'رقمي', r'رقم\s*جوال', r'تواصل\s*خارج',
        r'سناب', r'انستا', r'تليجرام', r'ايميل',
        r'insta', r'snap(?:chat)?', r'telegram', r'tele',
    ]),
    re.IGNORECASE | re.UNICODE,
)

_ALL_PATTERNS = [_PHONE_RE, _EMAIL_RE, _URL_RE, _HANDLE_RE, _KEYWORDS_RE]


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Single-message masking
# ═══════════════════════════════════════════════════════════════════════════════

def mask_contact_info(text: str) -> tuple[str, bool]:
    """
    Replace contact information in *text* with a safe placeholder.
    Returns (masked_text, was_flagged).

    Handles: phone numbers, emails, URLs, social handles, off-platform keywords,
    spelled-out digit sequences (English + Arabic), Arabic-Indic numerals.
    Protects: prices (SAR/ريال context), model years (2020-2039).
    """
    if not text:
        return text, False

    # Protect prices and years from false-positive masking
    protected_text, protected_map = _protect_prices_and_years(text)

    # Normalise spelled-out digits and Arabic-Indic numerals for detection
    normalised = _normalise_to_digits(protected_text)

    # Check if the normalised version has phone patterns the original didn't
    flagged = False
    result = protected_text

    # First pass: standard patterns on original text
    for pattern in _ALL_PATTERNS:
        new_result = pattern.sub(_PLACEHOLDER, result)
        if new_result != result:
            flagged = True
            result = new_result

    # Second pass: check normalised text for phone patterns (catches spelled digits)
    if not flagged:
        for pattern in [_PHONE_RE]:
            if pattern.search(normalised):
                # The normalised text has a phone number — mask the entire message
                flagged = True
                result = _PLACEHOLDER
                break

    # Restore protected prices/years
    result = _restore_protected(result, protected_map)
    return result, flagged


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Cross-message aggregation (split-number detection)
# ═══════════════════════════════════════════════════════════════════════════════

_DIGITS_ONLY_RE = re.compile(r'[^\d]')
_SAUDI_PREFIX_RE = re.compile(r'^(?:05|9665|\+9665|009665)')


def mask_conversation_messages(
    messages: list,
    reader_id: int,
    allow_contact: bool = False,
    is_admin: bool = False,
) -> list[str]:
    """
    Mask a list of message objects for a given reader. Each message must have
    .content, .sender_id attributes. Returns a list of masked content strings
    in the same order.

    Applies single-message masking, then cross-message aggregation to detect
    phone numbers split across consecutive messages from the same sender.
    """
    if allow_contact or is_admin:
        return [m.content for m in messages]

    # Step 1: single-message masking
    masked = []
    for m in messages:
        text, _ = mask_contact_info(m.content or '')
        masked.append(text)

    # Step 2: cross-message aggregation (sliding window of 5 consecutive
    # messages from the same sender)
    WINDOW = 5
    for i in range(len(messages)):
        sender = messages[i].sender_id
        # Collect consecutive messages from this sender (looking back)
        run_indices = []
        for j in range(max(0, i - WINDOW + 1), i + 1):
            if messages[j].sender_id == sender:
                run_indices.append(j)
            else:
                run_indices = []  # break the run

        if len(run_indices) < 2:
            continue

        # Extract digits from the original content of these messages
        combined_digits = ''
        for idx in run_indices:
            raw = messages[idx].content or ''
            normalised = _normalise_to_digits(raw)
            digits = _DIGITS_ONLY_RE.sub('', normalised)
            combined_digits += digits

        # Check if combined digits form a phone-like number (9-13 digits, Saudi prefix)
        if 9 <= len(combined_digits) <= 15 and _SAUDI_PREFIX_RE.match(combined_digits):
            # Mask all messages in this run
            for idx in run_indices:
                if masked[idx] != _PLACEHOLDER:
                    masked[idx] = _PLACEHOLDER

    return masked


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Order-based contact gate
# ═══════════════════════════════════════════════════════════════════════════════

_PAID_STATUSES = frozenset({
    'deposit_paid', 'confirmed', 'sourcing', 'purchased',
    'preparing_shipment', 'shipped', 'arrived_port', 'in_customs',
    'customs_cleared', 'inspection', 'ready', 'delivered', 'completed',
})


def get_allow_contact(conversation) -> bool:
    """
    Return True if an ImportOrder exists between the conversation's buyer
    and importer (seller) with status at or past deposit_paid.
    """
    from orders.models import ImportOrder

    return ImportOrder.objects.filter(
        buyer_id=conversation.buyer_id,
        importer_id=conversation.seller_id,
        status__in=_PAID_STATUSES,
    ).exists()


def strict_digit_mask(text: str) -> tuple[str, bool]:
    """
    Extra pre-payment defence: masks ANY unprotected run of 3+ digits.
    Catches phone numbers split across multiple messages ("055584" … "0131"),
    which the per-message pattern matcher cannot see. Prices with SAR/ريال
    context and model years stay untouched. Only applied BEFORE full payment.
    """
    if not text:
        return text, False
    protected_text, protected_map = _protect_prices_and_years(text)
    # Normalise Arabic-Indic numerals so ٠٥٥ is caught too
    normalised = protected_text.translate(_ARABIC_INDIC_MAP)
    masked = re.sub(r'(?<!\d)\d{3,}(?!\d)', _PLACEHOLDER, normalised)
    flagged = masked != normalised
    for token, original in protected_map.items():
        masked = masked.replace(token, original)
    return masked, flagged
