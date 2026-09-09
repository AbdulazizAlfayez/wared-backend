"""
Management command: load_sample_data
Populates the database with realistic Saudi market sample data.
Idempotent — safe to run multiple times.
"""
import random
import string
from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _vin():
    """Generate a random valid-format 17-char VIN."""
    chars = string.ascii_uppercase.replace("I", "").replace("O", "").replace("Q", "") + string.digits
    return "".join(random.choices(chars, k=17))


def _phone():
    """Generate a random Saudi mobile number."""
    return f"05{random.randint(10000000, 99999999)}"


def _coords(city_name):
    """Return (lat, lng) for common Saudi cities."""
    _map = {
        "Riyadh":    (24.6877, 46.7219),
        "Jeddah":    (21.5433, 39.1728),
        "Dammam":    (26.4207, 50.0888),
        "Makkah":    (21.3891, 39.8579),
        "Madinah":   (24.5247, 39.5692),
        "Al Khobar": (26.2172, 50.1971),
        "Taif":      (21.2854, 40.4152),
        "Jubail":    (27.0046, 49.6586),
        "Tabuk":     (28.3835, 36.5662),
        "Buraydah":  (26.3260, 43.9750),
    }
    base = _map.get(city_name, (24.6877, 46.7219))
    return (
        base[0] + random.uniform(-0.05, 0.05),
        base[1] + random.uniform(-0.05, 0.05),
    )


# ---------------------------------------------------------------------------
# Data definitions
# ---------------------------------------------------------------------------

DEALER_ACCOUNTS = [
    ("dealer1@saudicarsale.com",  "Ahmed Al-Rashid",    "أحمد الراشد"),
    ("dealer2@saudicarsale.com",  "Mohammed Al-Qahtani","محمد القحطاني"),
    ("dealer3@saudicarsale.com",  "Khalid Al-Otaibi",   "خالد العتيبي"),
    ("dealer4@saudicarsale.com",  "Fahad Al-Dosari",    "فهد الدوسري"),
    ("dealer5@saudicarsale.com",  "Sultan Al-Shammari", "سلطان الشمري"),
    ("dealer6@saudicarsale.com",  "Omar Al-Harbi",      "عمر الحربي"),
    ("dealer7@saudicarsale.com",  "Saud Al-Mutairi",    "سعود المطيري"),
    ("dealer8@saudicarsale.com",  "Turki Al-Ghamdi",    "تركي الغامدي"),
    ("dealer9@saudicarsale.com",  "Nawaf Al-Zahrani",   "نواف الزهراني"),
    ("dealer10@saudicarsale.com", "Faisal Al-Sulami",   "فيصل السلمي"),
]

# city_key → (city_name, city_id)
CITIES = {
    "riyadh":    ("Riyadh",    47),
    "jeddah":    ("Jeddah",    7),
    "dammam":    ("Dammam",    11),
    "makkah":    ("Makkah",    6),
    "madinah":   ("Madinah",   18),
    "khobar":    ("Al Khobar", 13),
    "taif":      ("Taif",      8),
    "jubail":    ("Jubail",    14),
    "tabuk":     ("Tabuk",     29),
    "buraydah":  ("Buraydah",  26),
}

# Weighted city distribution (city_key: weight)
CITY_WEIGHTS = {
    "riyadh":  40,
    "jeddah":  25,
    "dammam":  15,
    "makkah":  10,
    "madinah": 5,
    "khobar":  5,
}


def _pick_city():
    """Return (city_name, city_id) weighted by CITY_WEIGHTS."""
    keys = list(CITY_WEIGHTS.keys())
    weights = [CITY_WEIGHTS[k] for k in keys]
    chosen = random.choices(keys, weights=weights, k=1)[0]
    return CITIES[chosen]


COLORS = [
    ("White", "أبيض"), ("Pearl White", "أبيض لؤلؤي"), ("Silver", "فضي"),
    ("Black", "أسود"), ("Gray", "رمادي"), ("Blue", "أزرق"),
    ("Red", "أحمر"), ("Champagne", "شامبانيا"), ("Beige", "بيج"),
]

INTERIOR_COLORS = [
    ("Beige", "بيج"), ("Black", "أسود"), ("Brown", "بني"), ("Cream", "كريمي"),
]

DESCRIPTIONS_EN = [
    "Well maintained, single owner, full service history. GCC specs, agency maintained. Very clean interior and exterior.",
    "Excellent condition, low mileage, accident-free. Full agency service history. Selling due to upgrade.",
    "Original paint, no accidents, warranty remaining. GCC specification. Clean title, ready for transfer.",
    "Clean car, non-smoker, always garaged. Full service book stamped at agency. First owner.",
    "Pristine condition, fully loaded with all options. GCC specs, customs cleared. Serious buyers only.",
    "Excellent engine, smooth transmission. Recently serviced. All tires new. AC works perfectly.",
    "Family car, very well maintained. No accidents, no modifications. Original everything.",
    "Import specs, recently cleared customs. All papers complete. Ready for immediate transfer.",
    "Like new condition, barely driven. Still under factory warranty. All options included.",
    "Motivated seller, price negotiable. Clean CarFax, single owner. All service records available.",
]

DESCRIPTIONS_AR = [
    "سيارة نظيفة بحالة ممتازة، مالك واحد، صيانة وكالة كاملة، مواصفات خليجية.",
    "بحالة ممتازة، كيلومترات منخفضة، خالية من الحوادث، تاريخ صيانة كامل من الوكالة.",
    "دهان أصلي، بدون حوادث، ضمان سار. مواصفات خليجية. صك نظيف.",
    "سيارة نظيفة، غير مدخن، دائمًا في مرآب. كتيب الخدمة الكامل مختوم من الوكالة.",
    "حالة ممتازة، مجهزة بجميع الخيارات. مواصفات خليجية. جاد في البيع فقط.",
    "محرك ممتاز، ناقل حركة سلس. خدمة حديثة. جميع الإطارات جديدة. المكيف يعمل بشكل مثالي.",
    "سيارة عائلية، صيانة ممتازة. بدون حوادث، بدون تعديلات. كل شيء أصلي.",
    "مواصفات أمريكية، تم إخلاء جمارك حديثًا. جميع الأوراق مكتملة.",
    "كالجديدة، أميال قليلة جدًا. لا تزال تحت ضمان المصنع. جميع الخيارات.",
    "البائع جاد، السعر قابل للتفاوض. مالك واحد. جميع سجلات الصيانة متاحة.",
]

# (make, make_ar, model, year, price_range, body_type, fuel, typical_mileage_range, condition)
LISTINGS_SPEC = [
    # Toyota — 12
    ("Toyota", "تويوتا", "Camry",      2023, (85000,  115000), "sedan",  "petrol",  (15000,  60000),  "used"),
    ("Toyota", "تويوتا", "Camry",      2024, (95000,  125000), "sedan",  "hybrid",  (5000,   25000),  "new"),
    ("Toyota", "تويوتا", "Land Cruiser",2022,(240000, 320000), "suv",    "petrol",  (30000,  90000),  "used"),
    ("Toyota", "تويوتا", "Land Cruiser",2024,(330000, 400000), "suv",    "petrol",  (5000,   25000),  "new"),
    ("Toyota", "تويوتا", "Hilux",      2023, (95000,  130000), "pickup", "diesel",  (20000,  70000),  "used"),
    ("Toyota", "تويوتا", "Hilux",      2024, (110000, 145000), "pickup", "diesel",  (5000,   20000),  "new"),
    ("Toyota", "تويوتا", "Corolla",    2022, (62000,  85000),  "sedan",  "petrol",  (20000,  70000),  "used"),
    ("Toyota", "تويوتا", "Corolla",    2023, (70000,  95000),  "sedan",  "petrol",  (10000,  45000),  "used"),
    ("Toyota", "تويوتا", "RAV4",       2023, (115000, 145000), "suv",    "hybrid",  (10000,  40000),  "used"),
    ("Toyota", "تويوتا", "Fortuner",   2022, (135000, 175000), "suv",    "diesel",  (25000,  75000),  "used"),
    ("Toyota", "تويوتا", "Avalon",     2023, (125000, 165000), "sedan",  "hybrid",  (10000,  50000),  "used"),
    ("Toyota", "تويوتا", "Prado",      2023, (195000, 245000), "suv",    "petrol",  (15000,  55000),  "used"),
    # Hyundai — 8
    ("Hyundai","هيونداي","Sonata",     2023, (65000,  90000),  "sedan",  "petrol",  (10000,  50000),  "used"),
    ("Hyundai","هيونداي","Sonata",     2024, (72000,  98000),  "sedan",  "petrol",  (3000,   15000),  "new"),
    ("Hyundai","هيونداي","Tucson",     2023, (88000,  118000), "suv",    "petrol",  (10000,  45000),  "used"),
    ("Hyundai","هيونداي","Tucson",     2024, (98000,  130000), "suv",    "petrol",  (3000,   15000),  "new"),
    ("Hyundai","هيونداي","Accent",     2022, (42000,  58000),  "sedan",  "petrol",  (20000,  80000),  "used"),
    ("Hyundai","هيونداي","Elantra",    2023, (58000,  78000),  "sedan",  "petrol",  (10000,  50000),  "used"),
    ("Hyundai","هيونداي","Santa Fe",   2024, (125000, 160000), "suv",    "petrol",  (3000,   15000),  "new"),
    ("Hyundai","هيونداي","Palisade",   2023, (145000, 185000), "suv",    "petrol",  (10000,  45000),  "used"),
    # Nissan — 6
    ("Nissan", "نيسان",  "Patrol",     2023, (185000, 245000), "suv",    "petrol",  (15000,  55000),  "used"),
    ("Nissan", "نيسان",  "Patrol",     2024, (210000, 275000), "suv",    "petrol",  (3000,   15000),  "new"),
    ("Nissan", "نيسان",  "Altima",     2023, (68000,  90000),  "sedan",  "petrol",  (10000,  50000),  "used"),
    ("Nissan", "نيسان",  "X-Trail",    2024, (105000, 135000), "suv",    "petrol",  (3000,   15000),  "new"),
    ("Nissan", "نيسان",  "Sunny",      2022, (38000,  52000),  "sedan",  "petrol",  (20000,  80000),  "used"),
    ("Nissan", "نيسان",  "Kicks",      2023, (72000,  95000),  "suv",    "petrol",  (10000,  45000),  "used"),
    # Chevrolet — 4
    ("Chevrolet","شيفروليه","Tahoe",   2023, (215000, 285000), "suv",    "petrol",  (15000,  55000),  "used"),
    ("Chevrolet","شيفروليه","Silverado",2024,(235000, 310000), "pickup", "petrol",  (5000,   25000),  "new"),
    ("Chevrolet","شيفروليه","Malibu",  2023, (72000,  95000),  "sedan",  "petrol",  (10000,  50000),  "used"),
    ("Chevrolet","شيفروليه","Traverse",2024,(175000, 225000), "suv",    "petrol",  (3000,   15000),  "new"),
    # Ford — 4
    ("Ford",    "فورد",   "Explorer",  2023, (165000, 215000), "suv",    "petrol",  (10000,  45000),  "used"),
    ("Ford",    "فورد",   "F-150",     2024, (195000, 255000), "pickup", "petrol",  (5000,   25000),  "new"),
    ("Ford",    "فورد",   "Taurus",    2022, (68000,  90000),  "sedan",  "petrol",  (20000,  75000),  "used"),
    ("Ford",    "فورد",   "Expedition",2023,(215000, 275000), "suv",    "petrol",  (10000,  45000),  "used"),
    # BMW — 4
    ("BMW",     "بي إم دبليو","X5",   2023, (245000, 325000), "suv",    "petrol",  (10000,  45000),  "used"),
    ("BMW",     "بي إم دبليو","530i", 2024, (215000, 285000), "sedan",  "petrol",  (3000,   15000),  "new"),
    ("BMW",     "بي إم دبليو","X3",   2023, (185000, 245000), "suv",    "petrol",  (10000,  45000),  "used"),
    ("BMW",     "بي إم دبليو","740i", 2024, (375000, 475000), "sedan",  "petrol",  (3000,   15000),  "new"),
    # Mercedes — 4
    ("Mercedes","مرسيدس","E300",       2023, (235000, 305000), "sedan",  "petrol",  (10000,  45000),  "used"),
    ("Mercedes","مرسيدس","GLE 450",    2024, (345000, 435000), "suv",    "petrol",  (3000,   15000),  "new"),
    ("Mercedes","مرسيدس","C200",       2023, (175000, 225000), "sedan",  "petrol",  (10000,  45000),  "used"),
    ("Mercedes","مرسيدس","S500",       2024, (595000, 745000), "sedan",  "petrol",  (3000,   15000),  "new"),
    # Lexus — 3
    ("Lexus",   "لكزس",  "ES350",      2023, (175000, 225000), "sedan",  "petrol",  (10000,  45000),  "used"),
    ("Lexus",   "لكزس",  "LX600",      2024, (495000, 625000), "suv",    "petrol",  (3000,   15000),  "new"),
    ("Lexus",   "لكزس",  "RX350",      2023, (215000, 275000), "suv",    "petrol",  (10000,  45000),  "used"),
    # GMC — 3
    ("GMC",     "جي إم سي","Yukon",    2023, (215000, 285000), "suv",    "petrol",  (10000,  45000),  "used"),
    ("GMC",     "جي إم سي","Sierra",   2024, (195000, 255000), "pickup", "petrol",  (3000,   15000),  "new"),
    ("GMC",     "جي إم سي","Terrain",  2023, (115000, 150000), "suv",    "petrol",  (10000,  45000),  "used"),
    # Kia — 2
    ("Kia",     "كيا",   "K5",         2024, (72000,  98000),  "sedan",  "petrol",  (3000,   15000),  "new"),
    ("Kia",     "كيا",   "Sportage",   2023, (92000,  122000), "suv",    "petrol",  (10000,  45000),  "used"),
]

# Showroom definitions
SHOWROOMS = [
    {
        "name":    "Al-Rajhi Motors",
        "name_ar": "الراجحي للسيارات",
        "city_key":"riyadh",
        "desc":    "One of Riyadh's premier Toyota and Japanese car showrooms. Established 2005.",
        "desc_ar": "أحد أبرز معارض سيارات تويوتا والسيارات اليابانية في الرياض. تأسس عام 2005.",
        "phone":   "0114321000",
        "whatsapp":"0554321000",
        "email":   "sales@alrajhimotors.sa",
        "website": "https://alrajhimotors.sa",
        "specs":   ["Toyota", "Lexus", "Financing", "Certified"],
        "verified": True,
        "cr":      "1010123456",
        "est_year": 2005,
        "dealer_idx": 0,
    },
    {
        "name":    "Elite Auto Gallery",
        "name_ar": "معرض النخبة للسيارات",
        "city_key":"riyadh",
        "desc":    "Riyadh's luxury car destination. Specializing in BMW, Mercedes, and Porsche.",
        "desc_ar": "وجهة السيارات الفاخرة في الرياض. متخصصون في بي إم دبليو ومرسيدس وبورش.",
        "phone":   "0114982000",
        "whatsapp":"0554982000",
        "email":   "info@eliteauto.sa",
        "website": "https://eliteauto.sa",
        "specs":   ["BMW", "Mercedes", "Porsche", "Luxury", "Financing"],
        "verified": True,
        "cr":      "1010234567",
        "est_year": 2010,
        "dealer_idx": 1,
    },
    {
        "name":    "Al-Jazeera Cars",
        "name_ar": "الجزيرة للسيارات",
        "city_key":"jeddah",
        "desc":    "Multi-brand showroom in Jeddah with over 200 cars in stock at all times.",
        "desc_ar": "معرض متعدد الماركات في جدة مع أكثر من 200 سيارة في المخزون دائمًا.",
        "phone":   "0122345678",
        "whatsapp":"0562345678",
        "email":   "sales@aljazeeracars.sa",
        "website": "https://aljazeeracars.sa",
        "specs":   ["Multi-brand", "Trade-in", "Financing", "Insurance"],
        "verified": True,
        "cr":      "4030345678",
        "est_year": 2008,
        "dealer_idx": 2,
    },
    {
        "name":    "Gulf Auto Showroom",
        "name_ar": "معرض الخليج للسيارات",
        "city_key":"dammam",
        "desc":    "Eastern Province's trusted multi-brand dealer. Specializing in American imports.",
        "desc_ar": "الوكيل الموثوق متعدد الماركات في المنطقة الشرقية. متخصص في السيارات الأمريكية.",
        "phone":   "0133456789",
        "whatsapp":"0573456789",
        "email":   "info@gulfauto.sa",
        "specs":   ["American Import", "Multi-brand", "Financing"],
        "verified": False,
        "cr":      "2050456789",
        "est_year": 2012,
        "dealer_idx": 3,
    },
    {
        "name":    "Al-Haramain Motors",
        "name_ar": "الحرمين للسيارات",
        "city_key":"makkah",
        "desc":    "Affordable quality cars in Makkah. Wide selection of used vehicles with warranty.",
        "desc_ar": "سيارات ذات جودة بأسعار مناسبة في مكة المكرمة. تشكيلة واسعة من السيارات المستعملة مع ضمان.",
        "phone":   "0122567890",
        "whatsapp":"0552567890",
        "email":   "info@alharaminmotors.sa",
        "specs":   ["Budget", "Used Cars", "Warranty", "Financing"],
        "verified": False,
        "cr":      "4034567890",
        "est_year": 2015,
        "dealer_idx": 4,
    },
    {
        "name":    "Riyadh International Motors",
        "name_ar": "معرض الرياض الدولي للسيارات",
        "city_key":"riyadh",
        "desc":    "Specialists in American and European imports. Full customs clearance service.",
        "desc_ar": "متخصصون في الاستيراد الأمريكي والأوروبي. خدمة إخلاء الجمارك الكاملة.",
        "phone":   "0114678901",
        "whatsapp":"0554678901",
        "email":   "sales@riyadhinternational.sa",
        "specs":   ["American Import", "European Import", "Financing", "Customs Clearance"],
        "verified": True,
        "cr":      "1010678901",
        "est_year": 2007,
        "dealer_idx": 5,
    },
    {
        "name":    "Saudi Luxury Cars",
        "name_ar": "السعودية للسيارات الفاخرة",
        "city_key":"riyadh",
        "desc":    "Exclusive dealer for BMW, Mercedes, and Lexus. Premium service guaranteed.",
        "desc_ar": "وكيل حصري لسيارات بي إم دبليو ومرسيدس ولكزس. خدمة مميزة مضمونة.",
        "phone":   "0114789012",
        "whatsapp":"0554789012",
        "email":   "info@saudiluxury.sa",
        "specs":   ["BMW", "Mercedes", "Lexus", "Premium Service"],
        "verified": True,
        "cr":      "1010789012",
        "est_year": 2011,
        "dealer_idx": 6,
    },
    {
        "name":    "Jeddah Central Motors",
        "name_ar": "معرض جدة المركزي للسيارات",
        "city_key":"jeddah",
        "desc":    "Centrally located in Jeddah. Best prices for family cars and SUVs.",
        "desc_ar": "موقع مركزي في جدة. أفضل الأسعار للسيارات العائلية وسيارات الدفع الرباعي.",
        "phone":   "0122890123",
        "whatsapp":"0562890123",
        "email":   "sales@jeddahcentral.sa",
        "specs":   ["Family Cars", "SUVs", "Trade-in", "Financing"],
        "verified": False,
        "cr":      "4030890123",
        "est_year": 2014,
        "dealer_idx": 7,
    },
    {
        "name":    "Eastern Province Cars",
        "name_ar": "الشرقية للسيارات",
        "city_key":"dammam",
        "desc":    "The Eastern Province's go-to for pickup trucks, SUVs, and heavy-duty vehicles.",
        "desc_ar": "الخيار الأول في المنطقة الشرقية لشاحنات البيك أب وسيارات الدفع الرباعي.",
        "phone":   "0133901234",
        "whatsapp":"0573901234",
        "email":   "info@easternprovincecars.sa",
        "specs":   ["Pickup Trucks", "SUVs", "Diesel", "Heavy Duty"],
        "verified": False,
        "cr":      "2050901234",
        "est_year": 2013,
        "dealer_idx": 8,
    },
    {
        "name":    "Madinah Auto Gallery",
        "name_ar": "معرض المدينة للسيارات",
        "city_key":"madinah",
        "desc":    "Serving Madinah with quality used cars and budget-friendly options.",
        "desc_ar": "نخدم المدينة المنورة بسيارات مستعملة عالية الجودة وخيارات بأسعار مناسبة.",
        "phone":   "0148012345",
        "whatsapp":"0568012345",
        "email":   "info@madinahauto.sa",
        "specs":   ["Budget", "Used Cars", "Warranty"],
        "verified": False,
        "cr":      "4031012345",
        "est_year": 2016,
        "dealer_idx": 9,
    },
]

# Showroom working hours: (day 0=Sun, is_closed, open, close)
SHOWROOM_HOURS = [
    (0, False, "09:00", "21:00"),  # Sunday
    (1, False, "09:00", "21:00"),  # Monday
    (2, False, "09:00", "21:00"),  # Tuesday
    (3, False, "09:00", "21:00"),  # Wednesday
    (4, False, "09:00", "21:00"),  # Thursday
    (5, False, "16:00", "21:00"),  # Friday
    (6, False, "10:00", "21:00"),  # Saturday
]

# Workshop definitions
WORKSHOPS = [
    {
        "name":    "Advanced Service Center",
        "name_ar": "مركز الصيانة المتقدم",
        "city_key":"riyadh",
        "desc":    "Full-service automotive workshop in Riyadh. Toyota, Hyundai, and Kia specialists.",
        "desc_ar": "ورشة سيارات متكاملة في الرياض. متخصصون في تويوتا وهيونداي وكيا.",
        "phone":   "0114112233",
        "whatsapp":"0554112233",
        "specs":   ["Full Service", "Toyota", "Hyundai", "Kia"],
        "dealer_idx": 0,
        "services": [
            ("Oil Change", "تغيير الزيت", "maintenance", 180, "fixed", 30),
            ("Full Service", "فحص شامل", "maintenance", 850, "fixed", 120),
            ("Engine Diagnostics", "تشخيص المحرك", "diagnostics", 220, "fixed", 45),
            ("Brake Pad Replacement", "تبديل تيل الفرامل", "repair", 480, "fixed", 90),
            ("AC Service", "صيانة المكيف", "ac", 320, "fixed", 60),
            ("Tire Rotation & Balance", "تدوير وضبط الإطارات", "tires", 120, "fixed", 45),
        ],
    },
    {
        "name":    "Engine Workshop",
        "name_ar": "ورشة المحركات",
        "city_key":"riyadh",
        "desc":    "Engine repair and rebuild specialists. Serving all major brands.",
        "desc_ar": "متخصصون في إصلاح وإعادة تجديد المحركات لجميع الماركات الكبرى.",
        "phone":   "0114223344",
        "whatsapp":"0554223344",
        "specs":   ["Engine Repair", "Engine Rebuild", "Diagnostics"],
        "dealer_idx": 1,
        "services": [
            ("Engine Diagnostics", "تشخيص المحرك", "diagnostics", 280, "fixed", 60),
            ("Engine Tune-Up", "ضبط المحرك", "engine", 650, "fixed", 120),
            ("Timing Belt Replacement", "تبديل حزام التوقيت", "engine", 1200, "fixed", 180),
            ("Oil Change", "تغيير الزيت", "maintenance", 160, "fixed", 30),
            ("Engine Rebuild", "إعادة تجديد المحرك", "engine", 8500, "starting_from", 480),
        ],
    },
    {
        "name":    "Brakes & Tires Center",
        "name_ar": "مركز الفرامل والإطارات",
        "city_key":"jeddah",
        "desc":    "Jeddah's leading brake and tire specialists. All brands, best prices.",
        "desc_ar": "المتخصص الأول في فرامل وإطارات السيارات في جدة. جميع الماركات بأفضل الأسعار.",
        "phone":   "0122334455",
        "whatsapp":"0562334455",
        "specs":   ["Brakes", "Tires", "Wheel Alignment"],
        "dealer_idx": 2,
        "services": [
            ("Brake Pad Replacement", "تبديل تيل الفرامل", "repair", 420, "fixed", 60),
            ("Brake Disc Replacement", "تبديل قرص الفرامل", "repair", 750, "starting_from", 90),
            ("Tire Rotation", "تدوير الإطارات", "tires", 80, "fixed", 30),
            ("Wheel Alignment", "ضبط زوايا العجلات", "tires", 150, "fixed", 45),
            ("Tire Replacement (per tire)", "تبديل إطار", "tires", 200, "starting_from", 20),
        ],
    },
    {
        "name":    "Electrical & AC Workshop",
        "name_ar": "ورشة الكهرباء والتكييف",
        "city_key":"dammam",
        "desc":    "Electrical systems and AC specialist workshop in Dammam. Fast diagnostics.",
        "desc_ar": "ورشة متخصصة في الأنظمة الكهربائية والتكييف في الدمام. تشخيص سريع.",
        "phone":   "0133445566",
        "whatsapp":"0573445566",
        "specs":   ["Electrical", "AC & Cooling", "Diagnostics"],
        "dealer_idx": 3,
        "services": [
            ("Electrical Diagnostics", "تشخيص كهربائي", "electrical", 250, "fixed", 45),
            ("AC Gas Refill", "شحن فريون المكيف", "ac", 280, "fixed", 30),
            ("AC Compressor Replacement", "تبديل كمبروسر المكيف", "ac", 1800, "starting_from", 120),
            ("Battery Replacement", "تبديل البطارية", "electrical", 450, "starting_from", 20),
            ("Alternator Repair", "إصلاح الدينامو", "electrical", 900, "starting_from", 90),
        ],
    },
    {
        "name":    "Body & Paint Center",
        "name_ar": "مركز السمكرة والدهان",
        "city_key":"riyadh",
        "desc":    "Professional body repair and painting. Dent removal and full paint jobs.",
        "desc_ar": "إصلاح هياكل ودهان احترافي. إزالة الدنتات والدهانات الكاملة.",
        "phone":   "0114556677",
        "whatsapp":"0554556677",
        "specs":   ["Body Work", "Paint", "Dent Removal", "Collision Repair"],
        "dealer_idx": 4,
        "services": [
            ("Panel Paint (per panel)", "دهان لوحة", "bodywork", 550, "starting_from", 120),
            ("Full Car Paint", "دهان كامل للسيارة", "bodywork", 5500, "starting_from", 480),
            ("Dent Removal", "إزالة دنتات", "bodywork", 350, "starting_from", 60),
            ("Bumper Repair", "إصلاح صدام", "bodywork", 650, "starting_from", 90),
            ("Windshield Replacement", "تبديل زجاج أمامي", "bodywork", 850, "starting_from", 60),
        ],
    },
    {
        "name":    "Transmission Shop",
        "name_ar": "ورشة ناقل الحركة",
        "city_key":"jeddah",
        "desc":    "Gearbox and transmission specialists. Automatic and manual, all makes.",
        "desc_ar": "متخصصون في صناديق السرعة وناقلات الحركة. أوتوماتيك وعادي لجميع الماركات.",
        "phone":   "0122667788",
        "whatsapp":"0562667788",
        "specs":   ["Transmission", "Gearbox", "Clutch"],
        "dealer_idx": 5,
        "services": [
            ("Transmission Service", "صيانة ناقل الحركة", "transmission", 1200, "starting_from", 120),
            ("Transmission Rebuild", "إعادة بناء ناقل الحركة", "transmission", 5500, "starting_from", 480),
            ("Clutch Replacement", "تبديل الكلتش", "transmission", 1800, "starting_from", 180),
            ("Transmission Fluid Change", "تغيير زيت ناقل الحركة", "transmission", 350, "fixed", 45),
            ("Diagnostics", "تشخيص", "diagnostics", 200, "fixed", 45),
        ],
    },
    {
        "name":    "Diagnostics Center",
        "name_ar": "مركز الفحص والبرمجة",
        "city_key":"riyadh",
        "desc":    "Advanced computer diagnostics and ECU programming for all modern vehicles.",
        "desc_ar": "تشخيص حاسوبي متقدم وبرمجة وحدات التحكم لجميع السيارات الحديثة.",
        "phone":   "0114778899",
        "whatsapp":"0554778899",
        "specs":   ["Diagnostics", "ECU Programming", "Computer Systems"],
        "dealer_idx": 6,
        "services": [
            ("Full Computer Scan", "فحص كمبيوتر شامل", "diagnostics", 280, "fixed", 60),
            ("ECU Programming", "برمجة وحدة التحكم", "diagnostics", 800, "starting_from", 90),
            ("Odometer Check", "فحص العداد", "diagnostics", 150, "fixed", 30),
            ("Pre-Purchase Inspection", "فحص قبل الشراء", "diagnostics", 350, "fixed", 60),
            ("ADAS Calibration", "معايرة أنظمة المساعدة", "diagnostics", 600, "starting_from", 90),
        ],
    },
    {
        "name":    "Detailing Studio",
        "name_ar": "ورشة تلميع وتنظيف السيارات",
        "city_key":"dammam",
        "desc":    "Premium car detailing in Dammam. Ceramic coating, paint correction, interior detailing.",
        "desc_ar": "تلميع وتنظيف احترافي في الدمام. طلاء سيراميك، تصحيح الدهان، تنظيف داخلي.",
        "phone":   "0133889900",
        "whatsapp":"0573889900",
        "specs":   ["Detailing", "Ceramic Coating", "Paint Correction"],
        "dealer_idx": 7,
        "services": [
            ("Basic Car Wash", "غسيل أساسي", "detailing", 80, "fixed", 30),
            ("Full Interior Detail", "تنظيف داخلي كامل", "detailing", 350, "fixed", 120),
            ("Exterior Polish", "تلميع خارجي", "detailing", 450, "starting_from", 120),
            ("Ceramic Coating", "طلاء سيراميك", "detailing", 2500, "starting_from", 300),
            ("Paint Correction", "تصحيح الدهان", "detailing", 1800, "starting_from", 240),
            ("Engine Bay Cleaning", "تنظيف حجرة المحرك", "detailing", 180, "fixed", 45),
        ],
    },
    {
        "name":    "Hyundai Service Center",
        "name_ar": "مركز صيانة هيونداي",
        "city_key":"riyadh",
        "desc":    "Authorized Hyundai and Kia maintenance workshop. Genuine parts guaranteed.",
        "desc_ar": "ورشة صيانة معتمدة لسيارات هيونداي وكيا. قطع غيار أصلية مضمونة.",
        "phone":   "0114990011",
        "whatsapp":"0554990011",
        "specs":   ["Hyundai", "Kia", "Genuine Parts", "Warranty Service"],
        "dealer_idx": 8,
        "services": [
            ("Oil Change", "تغيير الزيت", "maintenance", 200, "fixed", 30),
            ("Full Hyundai Service", "خدمة هيونداي الشاملة", "maintenance", 950, "fixed", 120),
            ("Brake Service", "صيانة الفرامل", "repair", 520, "fixed", 60),
            ("AC Service", "صيانة المكيف", "ac", 350, "fixed", 60),
            ("Electrical Diagnostics", "تشخيص كهربائي", "electrical", 220, "fixed", 45),
            ("Genuine Parts Installation", "تركيب قطع غيار أصلية", "other", 0, "contact", 60),
        ],
    },
    {
        "name":    "Toyota Specialist Workshop",
        "name_ar": "ورشة تويوتا المتخصصة",
        "city_key":"jeddah",
        "desc":    "Expert Toyota and Lexus servicing in Jeddah. 20+ years of experience.",
        "desc_ar": "خدمة متخصصة لسيارات تويوتا ولكزس في جدة. أكثر من 20 عامًا من الخبرة.",
        "phone":   "0122001122",
        "whatsapp":"0562001122",
        "specs":   ["Toyota", "Lexus", "Genuine Parts"],
        "dealer_idx": 9,
        "services": [
            ("Oil & Filter Change", "تغيير الزيت والفلتر", "maintenance", 220, "fixed", 30),
            ("Toyota Full Service", "خدمة تويوتا الشاملة", "maintenance", 1100, "fixed", 120),
            ("4WD System Service", "خدمة نظام الدفع الرباعي", "maintenance", 750, "starting_from", 90),
            ("Suspension Check", "فحص نظام التعليق", "repair", 200, "fixed", 45),
            ("Transmission Fluid Change", "تغيير زيت ناقل الحركة", "transmission", 380, "fixed", 45),
            ("Land Cruiser Specialist", "متخصص لاند كروزر", "maintenance", 1500, "starting_from", 180),
            ("Timing Chain Service", "خدمة سلسلة التوقيت", "engine", 2500, "starting_from", 240),
        ],
    },
]

REVIEW_COMMENTS_EN = [
    "Great service, fair prices. Highly recommend!",
    "Professional staff and clean showroom. Very satisfied.",
    "Excellent experience from start to finish. Will return.",
    "Good selection of cars but negotiation took time.",
    "Fast service and transparent pricing. No hidden fees.",
    "Very helpful team. Found exactly what I was looking for.",
    "Good prices but location is a bit hard to find.",
    "Outstanding service quality and genuine parts.",
    "Quick and efficient. Solved my car issue in one visit.",
    "Friendly staff, reasonable prices. Recommended.",
]

REVIEW_COMMENTS_AR = [
    "خدمة رائعة وأسعار مناسبة. أنصح بشدة!",
    "موظفون محترفون ومعرض نظيف. راضٍ جداً.",
    "تجربة ممتازة من البداية للنهاية. سأعود مرة أخرى.",
    "اختيار جيد من السيارات لكن التفاوض أخذ وقتاً.",
    "خدمة سريعة وأسعار شفافة. لا رسوم خفية.",
    "فريق مفيد جداً. وجدت بالضبط ما كنت أبحث عنه.",
    "أسعار جيدة لكن الموقع صعب الإيجاد بعض الشيء.",
    "جودة خدمة استثنائية وقطع غيار أصلية.",
    "سريع وفعال. حل مشكلة سيارتي في زيارة واحدة.",
    "موظفون ودودون وأسعار معقولة. موصى به.",
]

LEAD_MESSAGES = [
    "I'm interested in this car. Is it still available? Can we arrange a test drive?",
    "What is the lowest price you can offer? I'm a serious buyer.",
    "Is this car GCC spec? Does it have full service history?",
    "Can I see more photos? Specifically the interior and under the hood.",
    "I'm located in Riyadh. Can you deliver or arrange transport?",
    "هل السيارة متاحة للمعاينة؟ متى يمكنني الزيارة؟",
    "ما هو أقل سعر؟ أنا مشترٍ جاد ومستعد للتحويل الفوري.",
    "هل السيارة خالية من الحوادث؟ هل لديها صيانة وكالة؟",
    "هل يمكن التفاوض على السعر؟ سأدفع كاش.",
    "أريد فحص السيارة على حسابي قبل الشراء. هل توافق؟",
]


class Command(BaseCommand):
    help = "Load realistic Saudi market sample data (idempotent)"

    def handle(self, *args, **options):
        from django.contrib.auth import get_user_model
        from django.utils import timezone

        from cars.models import (
            Listing, Showroom, Workshop,
            ShowroomWorkingHours, ShowroomReview,
            WorkshopWorkingHours, WorkshopService, WorkshopReview,
        )
        from leads.models import Lead
        from locations.models import City
        from subscriptions.models import SubscriptionPlan, DealerSubscription

        User = get_user_model()

        counters = {
            "users": 0, "listings": 0, "showrooms": 0, "workshops": 0,
            "services": 0, "reviews": 0, "leads": 0,
        }

        # ── 1. Dealer accounts ────────────────────────────────────────
        self.stdout.write("Creating dealer accounts…")
        free_plan = SubscriptionPlan.objects.filter(slug="free").first()
        dealers = []

        for email, name, _name_ar in DEALER_ACCOUNTS:
            user, created = User.objects.get_or_create(
                email=email,
                defaults={
                    "name":     name,
                    "role":     "importer",
                    "is_active": True,
                },
            )
            if created:
                user.set_password("Test1234!")
                user.save()
                counters["users"] += 1
                # Give free subscription
                if free_plan:
                    DealerSubscription.objects.get_or_create(
                        dealer=user,
                        defaults={
                            "plan":         free_plan,
                            "billing_cycle":"monthly",
                            "status":       "active",
                            "started_at":   timezone.now(),
                            "expires_at":   timezone.now() + timedelta(days=365),
                        },
                    )
            dealers.append(user)

        # Collect all dealers (including pre-existing ones) for fallback
        all_dealers = list(User.objects.filter(role__in=["importer", "admin"]))
        if not all_dealers:
            self.stderr.write("No importer/admin users found. Aborting.")
            return

        # ── 2. Listings ───────────────────────────────────────────────
        self.stdout.write("Creating listings…")
        used_vins = set(Listing.objects.values_list("vin", flat=True))

        def fresh_vin():
            while True:
                v = _vin()
                if v not in used_vins:
                    used_vins.add(v)
                    return v

        # Determine how many more we need
        existing_count = Listing.objects.count()
        target = 50
        to_create = max(0, target - existing_count)

        new_listings = []

        for i, spec in enumerate(LISTINGS_SPEC):
            if i >= to_create:
                break

            make, make_ar, model, year, price_range, body_type, fuel, mileage_range, cond = spec
            city_name, city_id = _pick_city()
            lat, lng = _coords(city_name)
            color_en, color_ar = random.choice(COLORS)
            int_en, int_ar = random.choice(INTERIOR_COLORS)
            price = random.randint(*price_range)
            mileage = random.randint(*mileage_range)
            dealer = random.choice(dealers) if dealers else random.choice(all_dealers)

            status_choices = (
                ["approved"] * 8 + ["pending"] * 1 + ["draft"] * 1
            )
            status = random.choice(status_choices)
            imported_from = "local"
            if make in ("Ford", "Chevrolet", "GMC"):
                imported_from = random.choice(["american", "local", "gcc"])
            elif make in ("BMW", "Mercedes"):
                imported_from = random.choice(["european", "local"])

            title = f"{year} {make} {model}"
            vin = fresh_vin()

            try:
                city_obj = City.objects.get(id=city_id)
            except City.DoesNotExist:
                city_obj = City.objects.first()

            listing = Listing(
                title=title,
                make=make,
                make_ar=make_ar,
                model=model,
                year=year,
                price=price,
                mileage=mileage,
                city=city_name,
                city_ar=city_obj.name_ar if city_obj else "",
                city_obj=city_obj,
                latitude=lat,
                longitude=lng,
                fuel_type=fuel,
                transmission="automatic" if cond == "new" or random.random() > 0.15 else "manual",
                condition=cond,
                body_type=body_type,
                color=color_en,
                color_ar=color_ar,
                color_interior=int_en,
                color_interior_ar=int_ar,
                imported_from=imported_from,
                customs_cleared=True,
                accident_history=random.random() < 0.2,
                warranty_remaining=year >= 2022 and random.random() < 0.7,
                service_history=random.random() < 0.7,
                negotiable=random.random() < 0.6,
                vin=vin,
                status=status,
                is_active=True,
                owner=dealer,
                description=random.choice(DESCRIPTIONS_EN),
                description_ar=random.choice(DESCRIPTIONS_AR),
                view_count=random.randint(50, 500),
                unique_view_count=random.randint(30, 300),
            )
            # Skip clean() — batch insert bypasses status transition guard
            new_listings.append(listing)

        if new_listings:
            # Use bulk_create but call save() individually to avoid .clean() on status
            for l in new_listings:
                l.save()
                counters["listings"] += 1

        # Gather all listings for later use
        all_listings = list(Listing.objects.filter(is_active=True).order_by("-created_at"))

        # ── 3. Showrooms ──────────────────────────────────────────────
        self.stdout.write("Creating showrooms…")

        created_showrooms = []
        for idx, s_def in enumerate(SHOWROOMS):
            dealer = dealers[s_def["dealer_idx"]] if s_def["dealer_idx"] < len(dealers) else all_dealers[0]
            existing = Showroom.objects.filter(name=s_def["name"]).first()
            if existing:
                created_showrooms.append(existing)
                continue

            city_name, city_id = CITIES[s_def["city_key"]]
            lat, lng = _coords(city_name)
            try:
                city_obj = City.objects.get(id=city_id)
            except City.DoesNotExist:
                city_obj = City.objects.first()

            phone = s_def.get("phone", _phone())
            showroom = Showroom.objects.create(
                name=s_def["name"],
                name_ar=s_def["name_ar"],
                city=city_name,
                city_obj=city_obj,
                address=f"{random.randint(1,99)} {city_name} Street, {city_name}",
                address_ar=f"شارع {city_name} رقم {random.randint(1,99)}",
                latitude=lat,
                longitude=lng,
                phone=phone,
                whatsapp=s_def.get("whatsapp", phone),
                email=s_def.get("email", f"info@showroom{idx}.sa"),
                website=s_def.get("website", ""),
                description=s_def["desc"],
                description_ar=s_def["desc_ar"],
                specializations=s_def["specs"],
                commercial_registration=s_def.get("cr", ""),
                is_verified=s_def.get("verified", False),
                established_year=s_def.get("est_year"),
                is_active=True,
                owner=dealer,
                average_rating=round(random.uniform(3.5, 4.9), 1),
                total_reviews=random.randint(5, 50),
            )
            counters["showrooms"] += 1
            created_showrooms.append(showroom)

            # Working hours
            for day, is_closed, open_t, close_t in SHOWROOM_HOURS:
                ShowroomWorkingHours.objects.get_or_create(
                    showroom=showroom,
                    day=day,
                    defaults={
                        "is_closed":    is_closed,
                        "opening_time": open_t,
                        "closing_time": close_t,
                    },
                )

        # Assign some listings to showrooms
        listings_to_assign = [l for l in all_listings if l.showroom_id is None]
        random.shuffle(listings_to_assign)
        per_showroom = max(1, len(listings_to_assign) // len(created_showrooms))
        for i, showroom in enumerate(created_showrooms):
            batch = listings_to_assign[i * per_showroom:(i + 1) * per_showroom]
            for listing in batch:
                listing.showroom = showroom
                listing.save(update_fields=["showroom"])

        # ── 4. Workshops ──────────────────────────────────────────────
        self.stdout.write("Creating workshops…")

        all_users = list(User.objects.all())
        non_dealer_users = [u for u in all_users if u.role not in ("importer", "admin")] or all_users

        created_workshops = []
        for idx, w_def in enumerate(WORKSHOPS):
            dealer = dealers[w_def["dealer_idx"]] if w_def["dealer_idx"] < len(dealers) else all_dealers[0]
            existing = Workshop.objects.filter(name=w_def["name"]).first()
            if existing:
                created_workshops.append(existing)
                continue

            city_name, city_id = CITIES[w_def["city_key"]]
            lat, lng = _coords(city_name)
            try:
                city_obj = City.objects.get(id=city_id)
            except City.DoesNotExist:
                city_obj = City.objects.first()

            phone = w_def.get("phone", _phone())
            workshop = Workshop.objects.create(
                name=w_def["name"],
                name_ar=w_def["name_ar"],
                city=city_name,
                city_obj=city_obj,
                address=f"Industrial Area, {city_name}",
                address_ar=f"المنطقة الصناعية، {city_name}",
                latitude=lat,
                longitude=lng,
                phone=phone,
                whatsapp=w_def.get("whatsapp", phone),
                description=w_def["desc"],
                description_ar=w_def["desc_ar"],
                specializations=w_def["specs"],
                is_active=True,
                is_verified=random.choice([True, False]),
                owner=dealer,
                average_rating=round(random.uniform(3.5, 4.9), 1),
                total_reviews=random.randint(5, 30),
            )
            counters["workshops"] += 1
            created_workshops.append(workshop)

            # Working hours (same pattern)
            for day, is_closed, open_t, close_t in SHOWROOM_HOURS:
                WorkshopWorkingHours.objects.get_or_create(
                    workshop=workshop,
                    day=day,
                    defaults={
                        "is_closed":    is_closed,
                        "opening_time": open_t,
                        "closing_time": close_t,
                    },
                )

            # Services
            for svc in w_def.get("services", []):
                svc_name, svc_name_ar, category, price, price_type, duration = svc
                _, created = WorkshopService.objects.get_or_create(
                    workshop=workshop,
                    name=svc_name,
                    defaults={
                        "name_ar":         svc_name_ar,
                        "category":        category,
                        "price":           price if price_type != "contact" else None,
                        "price_type":      price_type,
                        "duration_minutes":duration,
                        "is_active":       True,
                    },
                )
                if created:
                    counters["services"] += 1

        # ── 5. Reviews ────────────────────────────────────────────────
        self.stdout.write("Creating reviews…")

        for showroom in created_showrooms:
            n = random.randint(3, 5)
            reviewers = random.sample(non_dealer_users, min(n, len(non_dealer_users)))
            for user in reviewers:
                _, created = ShowroomReview.objects.get_or_create(
                    showroom=showroom,
                    user=user,
                    defaults={
                        "rating":      random.randint(3, 5),
                        "title":       random.choice(REVIEW_COMMENTS_EN)[:80],
                        "comment":     random.choice(REVIEW_COMMENTS_AR),
                        "is_approved": True,
                    },
                )
                if created:
                    counters["reviews"] += 1

        for workshop in created_workshops:
            n = random.randint(2, 4)
            reviewers = random.sample(non_dealer_users, min(n, len(non_dealer_users)))
            for user in reviewers:
                _, created = WorkshopReview.objects.get_or_create(
                    workshop=workshop,
                    user=user,
                    defaults={
                        "rating":      random.randint(3, 5),
                        "title":       random.choice(REVIEW_COMMENTS_EN)[:80],
                        "comment":     random.choice(REVIEW_COMMENTS_AR),
                        "is_approved": True,
                    },
                )
                if created:
                    counters["reviews"] += 1

        # ── 6. Leads ──────────────────────────────────────────────────
        self.stdout.write("Creating leads…")

        approved_listings = [l for l in all_listings if l.status == "approved"]
        buyer_users = [u for u in all_users if u.role in ("user", "buyer")] or all_users
        lead_statuses = ["new"] * 10 + ["contacted"] * 6 + ["closed"] * 4
        lead_targets = min(20, len(approved_listings), len(buyer_users))

        for i in range(lead_targets):
            listing = random.choice(approved_listings)
            buyer = random.choice(buyer_users)
            if buyer == listing.owner:
                continue
            _, created = Lead.objects.get_or_create(
                listing=listing,
                buyer=buyer,
                defaults={
                    "dealer":        listing.owner,
                    "message":       random.choice(LEAD_MESSAGES),
                    "phone":         _phone(),
                    "email":         buyer.email,
                    "preferred_time":"morning",
                    "source":        random.choice(["website", "whatsapp", "phone"]),
                    "status":        random.choice(lead_statuses),
                },
            )
            if created:
                counters["leads"] += 1

        # ── Summary ───────────────────────────────────────────────────
        self.stdout.write(self.style.SUCCESS("\n✅ Sample data loaded successfully!\n"))
        self.stdout.write(f"  Users created:     {counters['users']}")
        self.stdout.write(f"  Listings created:  {counters['listings']}")
        self.stdout.write(f"  Showrooms created: {counters['showrooms']}")
        self.stdout.write(f"  Workshops created: {counters['workshops']}")
        self.stdout.write(f"  Services created:  {counters['services']}")
        self.stdout.write(f"  Reviews created:   {counters['reviews']}")
        self.stdout.write(f"  Leads created:     {counters['leads']}")
        self.stdout.write(f"\n  DB totals after run:")
        self.stdout.write(f"    Listings:  {Listing.objects.count()}")
        self.stdout.write(f"    Showrooms: {Showroom.objects.count()}")
        self.stdout.write(f"    Workshops: {Workshop.objects.count()}")
