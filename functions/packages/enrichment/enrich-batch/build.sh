#!/bin/bash
set -e

virtualenv virtualenv
source virtualenv/bin/activate
pip install --no-cache-dir -r requirements.txt

# ── Trim the virtualenv to fit DO Functions' 48MB limit ─────
SITE="virtualenv/lib/python*/site-packages"

# Remove pip, setuptools, and wheel (not needed at runtime)
rm -rf $SITE/pip $SITE/pip-* $SITE/setuptools $SITE/setuptools-* $SITE/pkg_resources
rm -rf $SITE/wheel $SITE/wheel-* $SITE/_distutils_hack

# Remove Django components not needed for ORM-only usage
rm -rf $SITE/django/contrib/admin/static
rm -rf $SITE/django/contrib/admin/templates
rm -rf $SITE/django/contrib/gis
rm -rf $SITE/django/contrib/admindocs
rm -rf $SITE/django/contrib/flatpages
rm -rf $SITE/django/contrib/sitemaps
rm -rf $SITE/django/contrib/syndication
rm -rf $SITE/django/contrib/redirects

# Remove Django translation files (.mo/.po) but keep locale directory structure
# (django.conf.locale is needed for Django to boot)
find $SITE/django -path '*/locale/*/LC_MESSAGES/*.mo' -delete 2>/dev/null || true
find $SITE/django -path '*/locale/*/LC_MESSAGES/*.po' -delete 2>/dev/null || true
# Remove non-English locale directories entirely from contrib apps
find $SITE/django/contrib -type d -name locale -exec sh -c 'find "$1" -mindepth 1 -maxdepth 1 -type d ! -name en -exec rm -rf {} +' _ {} \; 2>/dev/null || true

# Remove all __pycache__ and .pyc files
find virtualenv -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
find virtualenv -name '*.pyc' -delete 2>/dev/null || true

# Remove test directories
find $SITE -type d -name tests -exec rm -rf {} + 2>/dev/null || true
find $SITE -type d -name test -exec rm -rf {} + 2>/dev/null || true

# Remove dist-info (not needed at runtime)
find $SITE -type d -name '*.dist-info' -exec rm -rf {} + 2>/dev/null || true
find $SITE -type d -name '*.egg-info' -exec rm -rf {} + 2>/dev/null || true

echo "Virtualenv size after trim: $(du -sh virtualenv | cut -f1)"
