from alembic import command
from alembic.config import Config

from .config import ROOT, Settings


def migrate(settings: Settings | None = None):
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["settings"] = settings or Settings()
    command.upgrade(config, "head")


if __name__ == "__main__":
    migrate()
    print("PostgreSQL migrations applied.")
