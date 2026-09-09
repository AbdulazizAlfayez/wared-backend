from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('cars', '0016_bulk_upload'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # --- Step 1: Add promotion flags + metadata to Listing ---
        migrations.AddField(
            model_name='listing',
            name='is_featured',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='listing',
            name='is_highlighted',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='listing',
            name='is_top_search',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='listing',
            name='is_homepage',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='listing',
            name='promotion_expires_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='listing',
            name='promotion_priority',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddIndex(
            model_name='listing',
            index=models.Index(fields=['is_featured'], name='listing_is_featured_idx'),
        ),
        migrations.AddIndex(
            model_name='listing',
            index=models.Index(fields=['is_top_search'], name='listing_is_top_search_idx'),
        ),
        migrations.AddIndex(
            model_name='listing',
            index=models.Index(fields=['promotion_priority'], name='listing_promo_priority_idx'),
        ),

        # --- Step 2: Create PromotionPackage ---
        migrations.CreateModel(
            name='PromotionPackage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100)),
                ('slug', models.SlugField(unique=True)),
                ('description', models.TextField(blank=True, default='')),
                ('duration_days', models.PositiveIntegerField()),
                ('price', models.DecimalField(decimal_places=2, max_digits=10)),
                ('promotion_type', models.CharField(
                    choices=[
                        ('featured', 'Featured'),
                        ('highlighted', 'Highlighted'),
                        ('top_search', 'Top of Search'),
                        ('homepage', 'Homepage Banner'),
                    ],
                    max_length=30,
                )),
                ('priority', models.PositiveIntegerField(default=0)),
                ('is_active', models.BooleanField(default=True)),
                ('display_order', models.PositiveIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'db_table': 'promotion_packages',
                'ordering': ['display_order'],
            },
        ),

        # --- Step 3: Create ListingPromotion ---
        migrations.CreateModel(
            name='ListingPromotion',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(
                    choices=[
                        ('active', 'Active'),
                        ('expired', 'Expired'),
                        ('cancelled', 'Cancelled'),
                        ('pending_payment', 'Pending Payment'),
                    ],
                    default='active',
                    max_length=20,
                )),
                ('started_at', models.DateTimeField(auto_now_add=True)),
                ('expires_at', models.DateTimeField()),
                ('amount_paid', models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('listing', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='promotions',
                    to='cars.listing',
                )),
                ('package', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='promotions',
                    to='cars.promotionpackage',
                )),
                ('dealer', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='listing_promotions',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'db_table': 'listing_promotions',
                'ordering': ['-created_at'],
            },
        ),

        # --- Step 4: Indexes on ListingPromotion ---
        migrations.AddIndex(
            model_name='listingpromotion',
            index=models.Index(fields=['listing'], name='lp_listing_idx'),
        ),
        migrations.AddIndex(
            model_name='listingpromotion',
            index=models.Index(fields=['dealer'], name='lp_dealer_idx'),
        ),
        migrations.AddIndex(
            model_name='listingpromotion',
            index=models.Index(fields=['status'], name='lp_status_idx'),
        ),
        migrations.AddIndex(
            model_name='listingpromotion',
            index=models.Index(fields=['expires_at'], name='lp_expires_at_idx'),
        ),
        migrations.AddIndex(
            model_name='listingpromotion',
            index=models.Index(fields=['package'], name='lp_package_idx'),
        ),
    ]
