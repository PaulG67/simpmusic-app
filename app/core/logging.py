import sys

from loguru import logger

from app.core.settings import LOG_DIR, settings

logger.remove()
logger.add(sys.stdout, level=settings.log_level, enqueue=True)
logger.add(
    LOG_DIR / "simpmusic-app.log",
    level=settings.log_level,
    rotation="10 MB",
    retention=5,
    enqueue=True,
)

__all__ = ["logger"]
