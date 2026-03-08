"""Route modules for HelixOS API.

Each module defines an APIRouter with domain-specific endpoints.
"""

from src.routes.dashboard import router as dashboard_router
from src.routes.execution import router as execution_router
from src.routes.projects import router as projects_router
from src.routes.reviews import router as reviews_router
from src.routes.tasks import router as tasks_router

__all__ = [
    "dashboard_router",
    "execution_router",
    "projects_router",
    "reviews_router",
    "tasks_router",
]
