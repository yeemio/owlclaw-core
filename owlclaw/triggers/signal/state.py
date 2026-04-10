"""In-memory and database-backed state manager for signal pause/instruction control."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from owlclaw.triggers.signal.models import PendingInstruction, SignalSource
from owlclaw.triggers.signal.persistence import AgentControlStateORM, PendingInstructionORM


@dataclass(slots=True)
class AgentState:
    """One agent's mutable signal state."""

    paused: bool = False
    pending_instructions: list[PendingInstruction] = field(default_factory=list)


class AgentStateManager:
    """State operations for pause/resume and pending instructions."""

    def __init__(
        self,
        max_pending_instructions: int = 10,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self._max_pending_instructions = max_pending_instructions
        self._session_factory = session_factory

        # in-memory fallback when session_factory is not provided
        self._states: dict[tuple[str, str], AgentState] = {}
        self._lock = asyncio.Lock()

    @property
    def is_persistent(self) -> bool:
        return self._session_factory is not None

    async def get(self, agent_id: str, tenant_id: str) -> AgentState:
        if self._session_factory is None:
            return await self._get_in_memory(agent_id, tenant_id)
        return await self._get_from_db(agent_id, tenant_id)

    async def set_paused(self, agent_id: str, tenant_id: str, paused: bool) -> None:
        if self._session_factory is None:
            await self._set_paused_in_memory(agent_id, tenant_id, paused)
            return
        await self._set_paused_in_db(agent_id, tenant_id, paused)

    async def add_instruction(self, agent_id: str, tenant_id: str, instruction: PendingInstruction) -> None:
        if self._session_factory is None:
            await self._add_instruction_in_memory(agent_id, tenant_id, instruction)
            return
        await self._add_instruction_in_db(agent_id, tenant_id, instruction)

    async def consume_instructions(self, agent_id: str, tenant_id: str) -> list[PendingInstruction]:
        if self._session_factory is None:
            return await self._consume_instructions_in_memory(agent_id, tenant_id)
        return await self._consume_instructions_in_db(agent_id, tenant_id)

    async def cleanup_expired_instructions(self, agent_id: str, tenant_id: str) -> int:
        if self._session_factory is None:
            return await self._cleanup_expired_in_memory(agent_id, tenant_id)
        return await self._cleanup_expired_in_db(agent_id, tenant_id)

    async def _get_in_memory(self, agent_id: str, tenant_id: str) -> AgentState:
        async with self._lock:
            key = (tenant_id.strip(), agent_id.strip())
            return self._states.setdefault(key, AgentState())

    async def _set_paused_in_memory(self, agent_id: str, tenant_id: str, paused: bool) -> None:
        async with self._lock:
            key = (tenant_id.strip(), agent_id.strip())
            state = self._states.setdefault(key, AgentState())
            state.paused = paused

    async def _add_instruction_in_memory(self, agent_id: str, tenant_id: str, instruction: PendingInstruction) -> None:
        async with self._lock:
            key = (tenant_id.strip(), agent_id.strip())
            state = self._states.setdefault(key, AgentState())
            if len(state.pending_instructions) >= self._max_pending_instructions:
                state.pending_instructions.pop(0)
            state.pending_instructions.append(instruction)

    async def _consume_instructions_in_memory(self, agent_id: str, tenant_id: str) -> list[PendingInstruction]:
        async with self._lock:
            key = (tenant_id.strip(), agent_id.strip())
            state = self._states.setdefault(key, AgentState())
            now = datetime.now(timezone.utc)
            consumed: list[PendingInstruction] = []
            kept: list[PendingInstruction] = []
            for instruction in state.pending_instructions:
                if instruction.consumed or instruction.is_expired(now):
                    continue
                instruction.consumed = True
                consumed.append(instruction)
            for instruction in state.pending_instructions:
                if not instruction.consumed and not instruction.is_expired(now):
                    kept.append(instruction)
            state.pending_instructions = kept
            return consumed

    async def _cleanup_expired_in_memory(self, agent_id: str, tenant_id: str) -> int:
        async with self._lock:
            key = (tenant_id.strip(), agent_id.strip())
            state = self._states.setdefault(key, AgentState())
            now = datetime.now(timezone.utc)
            before = len(state.pending_instructions)
            state.pending_instructions = [item for item in state.pending_instructions if not item.is_expired(now) and not item.consumed]
            return before - len(state.pending_instructions)

    async def _get_from_db(self, agent_id: str, tenant_id: str) -> AgentState:
        assert self._session_factory is not None
        async with self._session_factory() as session:
            state = await session.scalar(
                select(AgentControlStateORM).where(
                    AgentControlStateORM.agent_id == agent_id,
                    AgentControlStateORM.tenant_id == tenant_id,
                )
            )
            if state is None:
                state = AgentControlStateORM(agent_id=agent_id, tenant_id=tenant_id, paused=False)
                session.add(state)
                await session.commit()

            rows = (
                await session.execute(
                    select(PendingInstructionORM).where(
                        PendingInstructionORM.agent_id == agent_id,
                        PendingInstructionORM.tenant_id == tenant_id,
                        PendingInstructionORM.consumed.is_(False),
                    )
                )
            ).scalars().all()
            return AgentState(paused=state.paused, pending_instructions=[self._to_instruction(r) for r in rows])

    async def _set_paused_in_db(self, agent_id: str, tenant_id: str, paused: bool) -> None:
        assert self._session_factory is not None
        async with self._session_factory() as session:
            row = await session.scalar(
                select(AgentControlStateORM).where(
                    AgentControlStateORM.agent_id == agent_id,
                    AgentControlStateORM.tenant_id == tenant_id,
                )
            )
            if row is None:
                row = AgentControlStateORM(agent_id=agent_id, tenant_id=tenant_id, paused=paused)
                session.add(row)
            else:
                row.paused = paused
            await session.commit()

    async def _add_instruction_in_db(self, agent_id: str, tenant_id: str, instruction: PendingInstruction) -> None:
        assert self._session_factory is not None
        async with self._session_factory() as session:
            count = int(
                await session.scalar(
                    select(func.count()).select_from(PendingInstructionORM).where(
                        PendingInstructionORM.agent_id == agent_id,
                        PendingInstructionORM.tenant_id == tenant_id,
                        PendingInstructionORM.consumed.is_(False),
                    )
                )
                or 0
            )

            if count >= self._max_pending_instructions:
                oldest = await session.scalar(
                    select(PendingInstructionORM)
                    .where(
                        PendingInstructionORM.agent_id == agent_id,
                        PendingInstructionORM.tenant_id == tenant_id,
                        PendingInstructionORM.consumed.is_(False),
                    )
                    .order_by(PendingInstructionORM.created_at.asc())
                )
                if oldest is not None:
                    await session.delete(oldest)

            row = PendingInstructionORM(
                tenant_id=tenant_id,
                agent_id=agent_id,
                content=instruction.content,
                operator=instruction.operator,
                source=instruction.source.value,
                created_at=instruction.created_at,
                expires_at=instruction.expires_at,
                consumed=instruction.consumed,
            )
            session.add(row)
            await session.commit()

    async def _consume_instructions_in_db(self, agent_id: str, tenant_id: str) -> list[PendingInstruction]:
        assert self._session_factory is not None
        async with self._session_factory() as session:
            now = datetime.now(timezone.utc)
            rows = (
                await session.execute(
                    select(PendingInstructionORM).where(
                        PendingInstructionORM.agent_id == agent_id,
                        PendingInstructionORM.tenant_id == tenant_id,
                        PendingInstructionORM.consumed.is_(False),
                        PendingInstructionORM.expires_at > now,
                    )
                )
            ).scalars().all()
            consumed: list[PendingInstruction] = []
            for row in rows:
                row.consumed = True
                row.consumed_at = now
                consumed.append(self._to_instruction(row))
            await session.commit()
            return consumed

    async def _cleanup_expired_in_db(self, agent_id: str, tenant_id: str) -> int:
        assert self._session_factory is not None
        async with self._session_factory() as session:
            now = datetime.now(timezone.utc)
            result = await session.execute(
                delete(PendingInstructionORM).where(
                    PendingInstructionORM.agent_id == agent_id,
                    PendingInstructionORM.tenant_id == tenant_id,
                    PendingInstructionORM.expires_at <= now,
                )
            )
            await session.commit()
            return int(getattr(result, "rowcount", 0) or 0)

    @staticmethod
    def _to_instruction(row: PendingInstructionORM) -> PendingInstruction:
        return PendingInstruction(
            content=row.content,
            operator=row.operator,
            source=SignalSource(row.source),
            created_at=row.created_at,
            expires_at=row.expires_at,
            consumed=row.consumed,
        )
