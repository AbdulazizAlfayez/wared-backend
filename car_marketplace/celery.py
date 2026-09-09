import os

from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'car_marketplace.settings')

app = Celery('car_marketplace')

# Read config from Django settings, using the CELERY_ namespace prefix.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Auto-discover tasks in all INSTALLED_APPS (looks for tasks.py in each app).
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f'Request: {self.request!r}')
