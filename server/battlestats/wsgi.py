import os
from django.core.wsgi import get_wsgi_application
from battlestats.env import load_default_env_files


load_default_env_files(os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'battlestats.settings')

application = get_wsgi_application()
