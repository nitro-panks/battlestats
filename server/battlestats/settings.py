import re
from pathlib import Path
import os
import logging.config
import socket
import sys

from battlestats.env import resolve_db_host, resolve_db_user

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY')

DEBUG = os.getenv('DJANGO_DEBUG', 'True').lower() in ('true', '1', 't')

ALLOWED_HOSTS = [
    host.strip() for host in os.getenv(
        'DJANGO_ALLOWED_HOSTS', '127.0.0.1,localhost'
    ).split(',') if host.strip()
]

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',
    'corsheaders',
    'celery',
    'django_celery_beat',
    'rest_framework',
    'warships',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.middleware.gzip.GZipMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'battlestats.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': ["templates/"],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'battlestats.wsgi.application'

db_sslmode = os.getenv('DB_SSLMODE', '').strip()
db_sslrootcert = os.getenv('DB_SSLROOTCERT', '').strip()
db_options = {}

if db_sslmode:
    db_options['sslmode'] = db_sslmode

if db_sslrootcert:
    db_options['sslrootcert'] = db_sslrootcert

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.{}'.format(
            os.getenv('DB_ENGINE', 'postgresql_psycopg2')
        ),
        'NAME': os.getenv('DB_NAME', 'battlestats'),
        'USER': resolve_db_user(),
        'PASSWORD': os.getenv('DB_PASSWORD', 'XVIB58E5rWnAsU6'),
        'HOST': resolve_db_host(),
        'PORT': os.getenv('DB_PORT', '5432'),
        'OPTIONS': db_options,
        'CONN_MAX_AGE': int(os.getenv('DB_CONN_MAX_AGE', '300')),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Internationalization
# https://docs.djangoproject.com/en/5.0/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True
USE_TZ = False

STATIC_URL = '/static/'

# STATICFILES_DIRS should not include STATIC_ROOT
STATICFILES_DIRS = [
    BASE_DIR / "staticfiles",
]

docker_id_pattern = r'^[a-fA-F0-9]{12}$'
# if re.match(docker_id_pattern, socket.gethostname()):
#     print("---> Using settings for Docker containers")
#     STATIC_ROOT = '/var/www/static/'
# else:
STATIC_ROOT = BASE_DIR / 'static'

STATICFILES_STORAGE = 'django.contrib.staticfiles.storage.StaticFilesStorage'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
CORS_ALLOWED_ORIGINS = [
    'http://localhost:8888',
    'http://localhost:8181',
    'http://localhost:3001',
]

# ── Caching ──────────────────────────────────────────────
REDIS_URL = os.getenv('REDIS_URL', '')
RUNNING_TESTS = 'test' in sys.argv

if RUNNING_TESTS:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'TIMEOUT': 60,
        }
    }
    LANDING_WARM_PARALLEL = False
elif REDIS_URL:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.redis.RedisCache',
            'LOCATION': REDIS_URL,
            'TIMEOUT': 60,
        }
    }
else:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'TIMEOUT': 60,
        }
    }

CORS_EXPOSE_HEADERS = [
    'X-Randoms-Updated-At',
    'X-Battles-Updated-At',
    'X-Ranked-Updated-At',
    'X-Clan-Battles-Pending',
    'X-Landing-Players-Cache-Mode',
    'X-Landing-Players-Cache-TTL-Seconds',
    'X-Landing-Players-Cache-Cached-At',
    'X-Landing-Players-Cache-Expires-At',
    'X-Landing-Clans-Cache-TTL-Seconds',
    'X-Landing-Clans-Cache-Cached-At',
    'X-Landing-Clans-Cache-Expires-At',
]

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_REFERRER_POLICY = 'same-origin'

DEFAULT_RENDERER_CLASSES = [
    'rest_framework.renderers.JSONRenderer',
]
if DEBUG:
    DEFAULT_RENDERER_CLASSES.append(
        'rest_framework.renderers.BrowsableAPIRenderer')

LOGGING_CONFIG = None

# Get loglevel from env
LOGLEVEL = os.getenv('DJANGO_LOGLEVEL', 'INFO').upper()

# Create logs directory if it doesn't exist
BASE_LOG_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = BASE_LOG_DIR / 'logs'
LOG_DIR.mkdir(exist_ok=True)

# Determine if running in Docker
is_docker = re.match(docker_id_pattern, socket.gethostname())

logging.config.dictConfig({
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'console': {
            'format': '%(asctime)s %(levelname)s [%(name)s:%(lineno)s] %(module)s %(process)d %(thread)d %(message)s',
        },
        'file': {
            'format': '%(asctime)s %(levelname)s [%(name)s:%(lineno)s] %(message)s',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'console',
        },
        'file': {
            'class': 'logging.FileHandler',
            'formatter': 'file',
            'filename': LOG_DIR / 'django.log',
        },
    },
    'loggers': {
        '': {
            'level': LOGLEVEL,
            'handlers': ['console'] if is_docker else ['console', 'file'],
        },
    },
})


# Celery settings
CELERY_BROKER_URL = os.getenv(
    'CELERY_BROKER_URL', 'amqp://guest:guest@rabbitmq:5672//')
CELERY_RESULT_BACKEND = os.getenv('CELERY_RESULT_BACKEND', 'rpc://')
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_BROKER_POOL_LIMIT = int(os.getenv('CELERY_BROKER_POOL_LIMIT', '10'))

CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = 'UTC'
CELERY_ENABLE_UTC = True

CELERY_TASK_IGNORE_RESULT = True
CELERY_TASK_TRACK_STARTED = False
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_WORKER_MAX_TASKS_PER_CHILD = int(
    os.getenv('CELERY_WORKER_MAX_TASKS_PER_CHILD', '200'))

CELERY_TASK_DEFAULT_QUEUE = 'default'
CELERY_TASK_ROUTES = {
    'warships.tasks.crawl_all_clans_task': {'queue': 'background'},
    'warships.tasks.incremental_player_refresh_task': {'queue': 'background'},
    'warships.tasks.incremental_ranked_data_task': {'queue': 'background'},
    'warships.tasks.refresh_efficiency_rank_snapshot_task': {'queue': 'background'},
    'warships.tasks.warm_hot_entity_caches_task': {'queue': 'background'},
    'warships.tasks.warm_landing_best_entity_caches_task': {'queue': 'background'},
    'warships.tasks.warm_landing_page_content_task': {'queue': 'background'},
    'warships.tasks.warm_clan_battle_summaries_task': {'queue': 'background'},
    'warships.tasks.warm_player_ranked_wr_battles_correlation_task': {'queue': 'background'},
    'warships.tasks.refill_landing_random_players_queue_task': {'queue': 'background'},
    'warships.tasks.refill_landing_random_clans_queue_task': {'queue': 'background'},
}

REST_FRAMEWORK = {
    'EXCEPTION_HANDLER': 'warships.exceptions.custom_exception_handler',
    'DEFAULT_RENDERER_CLASSES': DEFAULT_RENDERER_CLASSES,
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': os.getenv('DRF_THROTTLE_ANON_RATE', '120/minute'),
        'user': os.getenv('DRF_THROTTLE_USER_RATE', '600/minute'),
    },
}
