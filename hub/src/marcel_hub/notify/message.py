"""The contract's `PushNotification` (contracts/app-api.yaml), and the deep links it carries."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Category = Literal['approval', 'task', 'broke', 'digest']
Level = Literal['normal', 'urgent']

TITLE_MAX = 80
BODY_MAX = 300


class PushNotification(BaseModel):
    """The payload of every push, the same for every backend. Built only through `routing`."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    title: str = Field(max_length=TITLE_MAX)
    body: str = Field(max_length=BODY_MAX)
    category: Category
    level: Level
    deep_link: str = Field(pattern=r'^marcel://(task|message|approval)/[A-Za-z0-9_-]+$')
    collapse_key: str | None = None
    task_id: str | None = None
    approval_id: str | None = None

    @model_validator(mode='after')
    def _approval_has_ids(self) -> Self:
        if self.category == 'approval' and not (self.approval_id and self.task_id):
            raise ValueError('an approval push needs `approval_id` and `task_id`')
        return self

    def to_api(self) -> dict[str, str]:
        """The JSON the contract describes: unset fields left out."""
        return self.model_dump(exclude_none=True)


def task_link(task_id: str) -> str:
    return f'marcel://task/{task_id}'


def approval_link(approval_id: str) -> str:
    return f'marcel://approval/{approval_id}'


def message_link(message_id: str) -> str:
    return f'marcel://message/{message_id}'


def clip(text: str, limit: int) -> str:
    """Shorten `text` to `limit` characters, ending in `…` when it was cut."""
    text = ' '.join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + '…'
