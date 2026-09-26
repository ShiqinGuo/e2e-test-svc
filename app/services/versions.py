from sqlalchemy import func, select

from ..models import Resource
from ..store import create_resource, update_resource


async def add_version(session, scenario, body, user):
    # Caller locks the scenario; sequence+FK constraints independently protect concurrent version creation.
    number = (
        await session.scalar(select(func.max(Resource.version_number)).where(Resource.parent_id == scenario.id))
    ) or 0
    version = create_resource(
        session,
        "version",
        scenario.project_id,
        {**body, "scenarioId": str(scenario.id), "number": number + 1, "createdBy": str(user.id)},
        scenario.id,
        number + 1,
    )
    await session.flush()
    update_resource(scenario, {"currentVersionId": str(version.id)})
    await session.commit()
    return version.body
