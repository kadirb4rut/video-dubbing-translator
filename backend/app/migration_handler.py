"""On-demand Alembic migration entrypoint for serverless deployments."""

from pathlib import Path

from alembic import command
from alembic.config import Config


def handler(event, context):
    # In the Lambda image this resolves to /app; in the repository it resolves
    # to the project root's backend directory, both of which contain alembic/.
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(config, "head")
    return {"status": "ok", "revision": "head"}
