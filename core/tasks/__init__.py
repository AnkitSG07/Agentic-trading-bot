# Re-export celery_app so that:
#   celery -A core.tasks worker ...
# resolves correctly from docker-compose and CLI.
from core.tasks.celery_tasks import celery_app

__all__ = ["celery_app"]
