"""One-shot: for every existing Organization × every preinstalled skill,
create OrgSkillInstall + WorkspaceSkillBinding rows so users don't see a
behavior regression after M3 ships.

Usage:
    cd backend
    uv run python scripts/dev/auto_install_preinstalled_for_existing_orgs.py
"""

import asyncio

from sqlalchemy import select

from cubeplex.db.engine import async_session_maker
from cubeplex.models import (
    Organization,
    OrgSkillInstall,
    Skill,
    Workspace,
    WorkspaceSkillBinding,
)


async def main() -> None:
    async with async_session_maker() as session:
        orgs = (await session.execute(select(Organization))).scalars().all()
        skills = (
            (
                await session.execute(
                    select(Skill).where(Skill.source == "preinstalled")  # type: ignore[arg-type]
                )
            )
            .scalars()
            .all()
        )

        for org in orgs:
            workspaces = (
                (
                    await session.execute(
                        select(Workspace).where(
                            Workspace.org_id == org.id  # type: ignore[arg-type]
                        )
                    )
                )
                .scalars()
                .all()
            )

            for skill in skills:
                existing = (
                    await session.execute(
                        select(OrgSkillInstall).where(
                            OrgSkillInstall.org_id == org.id,  # type: ignore[arg-type]
                            OrgSkillInstall.skill_id == skill.id,  # type: ignore[arg-type]
                        )
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    continue

                install = OrgSkillInstall(
                    org_id=org.id,
                    skill_id=skill.id,
                    installed_version=skill.current_version,
                    installed_by_user_id="migration-script",
                )
                session.add(install)
                await session.flush()
                for ws in workspaces:
                    session.add(
                        WorkspaceSkillBinding(
                            org_id=org.id,
                            workspace_id=ws.id,
                            org_skill_install_id=install.id,
                            enabled=True,
                        )
                    )
        await session.commit()
        print(f"Auto-installed preinstalled skills for {len(orgs)} orgs.")


if __name__ == "__main__":
    asyncio.run(main())
