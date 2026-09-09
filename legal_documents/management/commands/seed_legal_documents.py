"""
Seed version 1.0.0 of Terms of Service, Privacy Policy, and Cookie Policy.
"""
from datetime import date

from django.core.management.base import BaseCommand

from legal_documents.models import LegalDocument

PLACEHOLDER_NOTE = (
    "PLACEHOLDER: This is a placeholder document. "
    "Real legal text must be drafted by a Saudi-licensed lawyer before launch. "
    "This document must comply with the Saudi Personal Data Protection Law (PDPL)."
)

DOCUMENTS = [
    {
        'document_type': 'terms_of_service',
        'version': '1.0.0',
        'content_en': f"""# Terms of Service — WARED

**Version 1.0.0**

{PLACEHOLDER_NOTE}

## 1. Acceptance of Terms
By using the WARED platform, you agree to these Terms of Service.

## 2. Platform Overview
WARED is a marketplace for imported cars in the Kingdom of Saudi Arabia. We connect buyers with verified importers.

## 3. User Accounts
- You must provide accurate information when registering.
- You are responsible for maintaining the security of your account.
- You must be at least 18 years old to use this platform.

## 4. Commission Model
- Buyers pay a fixed SAR 99 deposit to reserve a car.
- Importers pay a 1% commission on completed orders.
- Founding partners receive a 0.5% commission rate for the first year.

## 5. Prohibited Conduct
- Fraudulent listings or misrepresentation of vehicles.
- Harassment of other users.
- Violation of Saudi Arabian laws and regulations.

## 6. Limitation of Liability
WARED acts as a marketplace facilitator and is not a party to transactions between buyers and importers.

## 7. Governing Law
These terms are governed by the laws of the Kingdom of Saudi Arabia.

## 8. Changes to Terms
We may update these terms. Users will be notified and asked to re-accept updated versions.
""",
        'content_ar': f"""# شروط الاستخدام — وارد

**الإصدار 1.0.0**

{PLACEHOLDER_NOTE}

## 1. قبول الشروط
باستخدامك لمنصة وارد، فإنك توافق على شروط الاستخدام هذه.

## 2. نظرة عامة على المنصة
وارد هي سوق للسيارات المستوردة في المملكة العربية السعودية. نربط المشترين بمستوردين موثوقين.

## 3. حسابات المستخدمين
- يجب تقديم معلومات دقيقة عند التسجيل.
- أنت مسؤول عن الحفاظ على أمان حسابك.
- يجب أن يكون عمرك 18 عامًا على الأقل لاستخدام المنصة.

## 4. نموذج العمولة
- يدفع المشتري عربون 99 ريال لحجز السيارة.
- يدفع المستورد عمولة 1% على الطلبات المكتملة.
- الشركاء المؤسسون يحصلون على عمولة 0.5% في السنة الأولى.

## 5. السلوك المحظور
- الإعلانات الاحتيالية أو التضليل في وصف المركبات.
- مضايقة المستخدمين الآخرين.
- مخالفة أنظمة المملكة العربية السعودية.

## 6. حدود المسؤولية
وارد تعمل كمنصة وسيطة وليست طرفاً في المعاملات بين المشترين والمستوردين.

## 7. القانون المعمول به
تخضع هذه الشروط لأنظمة المملكة العربية السعودية.

## 8. تعديل الشروط
قد نقوم بتحديث هذه الشروط. سيتم إخطار المستخدمين وطلب إعادة الموافقة.
""",
    },
    {
        'document_type': 'privacy_policy',
        'version': '1.0.0',
        'content_en': f"""# Privacy Policy — WARED

**Version 1.0.0**

{PLACEHOLDER_NOTE}

## 1. Introduction
This Privacy Policy explains how WARED collects, uses, and protects your personal data in compliance with the Saudi Personal Data Protection Law (PDPL).

## 2. Data We Collect
- Account information: name, email, phone number
- Verification documents: national ID, commercial registration
- Transaction data: orders, payments, listings
- Usage data: IP address, browser type, pages visited
- Communications: messages between buyers and importers

## 3. How We Use Your Data
- To provide and improve our services
- To verify user identity and prevent fraud
- To process transactions
- To communicate with you about your account and orders
- To comply with legal obligations

## 4. Data Sharing
- We do not sell your personal data.
- We share data with importers/buyers only as necessary for transactions.
- We may share data with law enforcement when legally required.

## 5. Data Retention
- Account data: retained while account is active + 30 days after deletion request.
- Transaction records: retained for 5 years per Saudi commercial law.
- Audit logs: retained for legal compliance.

## 6. Your Rights (PDPL)
- Right to access your personal data
- Right to request data export (portable format)
- Right to request account deletion
- Right to withdraw consent for optional data processing
- Right to file a complaint with the Saudi Data and AI Authority (SDAIA)

## 7. Data Export
You can request a full export of your data at any time through your account settings.

## 8. Account Deletion
You can request account deletion. Your account will be deactivated immediately and permanently deleted after a 30-day grace period.

## 9. Cookies
See our Cookie Policy for details on cookie usage.

## 10. Contact
For privacy inquiries: privacy@wared.sa
""",
        'content_ar': f"""# سياسة الخصوصية — وارد

**الإصدار 1.0.0**

{PLACEHOLDER_NOTE}

## 1. مقدمة
توضح سياسة الخصوصية هذه كيف تجمع وارد بياناتك الشخصية وتستخدمها وتحميها وفقاً لنظام حماية البيانات الشخصية السعودي.

## 2. البيانات التي نجمعها
- معلومات الحساب: الاسم، البريد الإلكتروني، رقم الهاتف
- وثائق التحقق: الهوية الوطنية، السجل التجاري
- بيانات المعاملات: الطلبات، المدفوعات، الإعلانات
- بيانات الاستخدام: عنوان IP، نوع المتصفح، الصفحات المزارة
- الاتصالات: الرسائل بين المشترين والمستوردين

## 3. كيف نستخدم بياناتك
- لتقديم وتحسين خدماتنا
- للتحقق من هوية المستخدم ومنع الاحتيال
- لمعالجة المعاملات
- للتواصل معك بشأن حسابك وطلباتك
- للامتثال للالتزامات القانونية

## 4. مشاركة البيانات
- لا نبيع بياناتك الشخصية.
- نشارك البيانات مع المستوردين/المشترين فقط حسب الحاجة.
- قد نشارك البيانات مع الجهات الحكومية عند الطلب قانونياً.

## 5. الاحتفاظ بالبيانات
- بيانات الحساب: محفوظة طوال فترة النشاط + 30 يوماً بعد طلب الحذف.
- سجلات المعاملات: محفوظة لمدة 5 سنوات حسب النظام التجاري السعودي.
- سجلات التدقيق: محفوظة للامتثال القانوني.

## 6. حقوقك (نظام حماية البيانات الشخصية)
- حق الوصول إلى بياناتك الشخصية
- حق طلب تصدير البيانات
- حق طلب حذف الحساب
- حق سحب الموافقة على المعالجة الاختيارية
- حق تقديم شكوى لهيئة البيانات والذكاء الاصطناعي (سدايا)

## 7. تصدير البيانات
يمكنك طلب تصدير كامل لبياناتك في أي وقت من إعدادات حسابك.

## 8. حذف الحساب
يمكنك طلب حذف حسابك. سيتم تعطيل حسابك فوراً وحذفه نهائياً بعد فترة سماح 30 يوماً.

## 9. ملفات تعريف الارتباط
راجع سياسة ملفات تعريف الارتباط للتفاصيل.

## 10. التواصل
لاستفسارات الخصوصية: privacy@wared.sa
""",
    },
    {
        'document_type': 'cookie_policy',
        'version': '1.0.0',
        'content_en': f"""# Cookie Policy — WARED

**Version 1.0.0**

{PLACEHOLDER_NOTE}

## 1. What Are Cookies
Cookies are small files stored on your device when you visit our platform.

## 2. Essential Cookies (Required)
- Authentication tokens (JWT)
- CSRF protection tokens
- Session management
These are necessary for the platform to function and cannot be disabled.

## 3. Analytics Cookies (Optional)
- Usage patterns and page views
- Performance monitoring
You can opt out of analytics cookies.

## 4. Marketing Cookies (Optional)
- Currently not used.
You can opt out of marketing cookies.

## 5. Managing Your Preferences
You can update your cookie preferences at any time through the cookie settings banner.
""",
        'content_ar': f"""# سياسة ملفات تعريف الارتباط — وارد

**الإصدار 1.0.0**

{PLACEHOLDER_NOTE}

## 1. ما هي ملفات تعريف الارتباط
ملفات تعريف الارتباط هي ملفات صغيرة تُخزن على جهازك عند زيارة منصتنا.

## 2. ملفات تعريف الارتباط الأساسية (مطلوبة)
- رموز المصادقة (JWT)
- رموز حماية CSRF
- إدارة الجلسات
هذه ضرورية لعمل المنصة ولا يمكن تعطيلها.

## 3. ملفات تعريف الارتباط التحليلية (اختيارية)
- أنماط الاستخدام ومشاهدات الصفحات
- مراقبة الأداء
يمكنك إلغاء الاشتراك في ملفات تعريف الارتباط التحليلية.

## 4. ملفات تعريف الارتباط التسويقية (اختيارية)
- غير مستخدمة حالياً.
يمكنك إلغاء الاشتراك في ملفات تعريف الارتباط التسويقية.

## 5. إدارة تفضيلاتك
يمكنك تحديث تفضيلات ملفات تعريف الارتباط في أي وقت من خلال إعدادات الخصوصية.
""",
    },
]


class Command(BaseCommand):
    help = 'Seed initial legal documents (Terms of Service, Privacy Policy, Cookie Policy) v1.0.0'

    def handle(self, *args, **options):
        today = date.today()
        created_count = 0

        for doc_data in DOCUMENTS:
            obj, created = LegalDocument.objects.get_or_create(
                document_type=doc_data['document_type'],
                version=doc_data['version'],
                defaults={
                    'content_en': doc_data['content_en'],
                    'content_ar': doc_data['content_ar'],
                    'effective_date': today,
                    'is_active': True,
                },
            )
            if created:
                created_count += 1
                self.stdout.write(self.style.SUCCESS(
                    f"  Created {obj.get_document_type_display()} v{obj.version}"
                ))
            else:
                self.stdout.write(self.style.WARNING(
                    f"  Already exists: {obj.get_document_type_display()} v{obj.version}"
                ))

        self.stdout.write(self.style.SUCCESS(f"\nDone. {created_count} document(s) created."))
