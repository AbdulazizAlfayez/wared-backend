from django.db import migrations

COUNTRIES = [
    {
        "code": "usa",
        "name_en": "United States",
        "name_ar": "الولايات المتحدة",
        "iso_code": "US",
        "flag_emoji": "🇺🇸",
        "latitude": 37.0902,
        "longitude": -95.7129,
        "avg_shipping_days": 35,
        "avg_shipping_cost_sar": 6000,
        "display_order": 10,
        "description": "Source for American muscle cars, trucks, and SUVs. Major auction houses: Copart, IAAI, Manheim.",
        "description_ar": "مصدر للسيارات الأمريكية العضلية والشاحنات وسيارات الدفع الرباعي. أبرز المزادات: كوبارت، آي إيه آي آي، مانهايم.",
    },
    {
        "code": "japan",
        "name_en": "Japan",
        "name_ar": "اليابان",
        "iso_code": "JP",
        "flag_emoji": "🇯🇵",
        "latitude": 36.2048,
        "longitude": 138.2529,
        "avg_shipping_days": 25,
        "avg_shipping_cost_sar": 5000,
        "display_order": 20,
        "description": "Japan's strict inspections mean low-mileage, well-maintained vehicles. USS, TAA, and JAA auctions.",
        "description_ar": "الفحوصات اليابانية الصارمة تعني سيارات بعداد منخفض وصيانة ممتازة. مزادات USS وTAA وJAA.",
    },
    {
        "code": "korea",
        "name_en": "South Korea",
        "name_ar": "كوريا الجنوبية",
        "iso_code": "KR",
        "flag_emoji": "🇰🇷",
        "latitude": 35.9078,
        "longitude": 127.7669,
        "avg_shipping_days": 22,
        "avg_shipping_cost_sar": 4500,
        "display_order": 30,
        "description": "Home of Hyundai and Kia. Competitive pricing and short shipping times to the Gulf.",
        "description_ar": "موطن هيونداي وكيا. أسعار تنافسية وأوقات شحن قصيرة إلى الخليج.",
    },
    {
        "code": "uae",
        "name_en": "United Arab Emirates",
        "name_ar": "الإمارات العربية المتحدة",
        "iso_code": "AE",
        "flag_emoji": "🇦🇪",
        "latitude": 23.4241,
        "longitude": 53.8478,
        "avg_shipping_days": 5,
        "avg_shipping_cost_sar": 1500,
        "display_order": 40,
        "description": "Nearest source market. GCC-spec vehicles with minimal customs friction. Overland delivery available.",
        "description_ar": "أقرب سوق مصدّر. سيارات بمواصفات خليجية مع إجراءات جمركية سهلة. التوصيل البري متاح.",
    },
    {
        "code": "germany",
        "name_en": "Germany",
        "name_ar": "ألمانيا",
        "iso_code": "DE",
        "flag_emoji": "🇩🇪",
        "latitude": 51.1657,
        "longitude": 10.4515,
        "avg_shipping_days": 30,
        "avg_shipping_cost_sar": 7000,
        "display_order": 50,
        "description": "European luxury and performance. Porsche, BMW, Mercedes direct from the source.",
        "description_ar": "الفخامة والأداء الأوروبي. بورشه، بي إم دبليو، مرسيدس مباشرة من المصدر.",
    },
    {
        "code": "canada",
        "name_en": "Canada",
        "name_ar": "كندا",
        "iso_code": "CA",
        "flag_emoji": "🇨🇦",
        "latitude": 56.1304,
        "longitude": -106.3468,
        "avg_shipping_days": 40,
        "avg_shipping_cost_sar": 6500,
        "display_order": 60,
        "description": "Similar inventory to the US with lower auction prices. Cold-climate vehicles in excellent condition.",
        "description_ar": "مخزون مشابه لأمريكا بأسعار مزادات أقل. سيارات مناخ بارد بحالة ممتازة.",
    },
    {
        "code": "gbr",
        "name_en": "United Kingdom",
        "name_ar": "المملكة المتحدة",
        "iso_code": "GB",
        "flag_emoji": "🇬🇧",
        "latitude": 55.3781,
        "longitude": -3.4360,
        "avg_shipping_days": 28,
        "avg_shipping_cost_sar": 7000,
        "display_order": 70,
        "description": "British luxury: Range Rover, Bentley, Aston Martin. Note: most are right-hand drive.",
        "description_ar": "الفخامة البريطانية: رينج روفر، بنتلي، أستون مارتن. ملاحظة: معظمها بمقود يميني.",
    },
    {
        "code": "other",
        "name_en": "Other",
        "name_ar": "أخرى",
        "iso_code": "XX",
        "flag_emoji": "🌐",
        "latitude": 0,
        "longitude": 0,
        "avg_shipping_days": 35,
        "avg_shipping_cost_sar": 6000,
        "display_order": 999,
        "description": "Vehicles sourced from other markets worldwide.",
        "description_ar": "سيارات من أسواق أخرى حول العالم.",
    },
]


def seed(apps, schema_editor):
    SourceCountry = apps.get_model("source_countries", "SourceCountry")
    for row in COUNTRIES:
        SourceCountry.objects.update_or_create(code=row["code"], defaults=row)


def unseed(apps, schema_editor):
    SourceCountry = apps.get_model("source_countries", "SourceCountry")
    codes = [c["code"] for c in COUNTRIES]
    SourceCountry.objects.filter(code__in=codes).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("source_countries", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
