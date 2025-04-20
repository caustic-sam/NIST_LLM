from typing import Generic, Optional, TypeVar

from pydantic import BaseModel
from pydantic._internal._model_construction import ModelMetaclass

T = TypeVar("T")


class Paged(BaseModel, Generic[T]):
    """Page a model result set"""

    total: int
    results: list[T]

    @property
    def first(self) -> T:
        return self.results[0]


def paginated(model: ModelMetaclass) -> ModelMetaclass:
    class PaginatedModel(BaseModel):
        total: int
        results: list[model]
        skip: Optional[int] = None
        limit: Optional[int] = None

    cls_name = f"Paginated<{model.__name__}>"
    PaginatedModel.__name__ = cls_name
    PaginatedModel.__qualname__ = cls_name

    return PaginatedModel
