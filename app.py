"""Vercel entry point. No background threads or writable local state."""
import os

from cloud_service import service_from_env
from webapp import create_app

app = create_app(service_from_env(),
                 secret=os.environ['LIBRARY_SECRET_KEY'], secure_cookie=True)
