from dataclasses import dataclass

from starlette.requests import HTTPConnection

from .config import Settings
from .db import Database
from .jobs import Jobs
from .runtime import RuntimePort
from .security import Vault


@dataclass(frozen=True)
class Context:
    settings: Settings
    db: Database
    vault: Vault
    runtime: RuntimePort
    jobs: Jobs


def get_context(connection: HTTPConnection) -> Context:
    """The same composition root serves HTTP and authenticated WebSockets."""
    return connection.app.state.context
