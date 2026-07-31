from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, StringConstraints, model_validator

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
SerializedDecimal = Annotated[Decimal, PlainSerializer(lambda value: f"{value:.3f}", return_type=str)]


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class Scope(StrEnum):
    GLOBAL = "global"
    CUSTOM = "custom"


class ScopeFilter(StrEnum):
    ALL = "all"
    GLOBAL = "global"
    CUSTOM = "custom"


class MeasurementFamily(StrEnum):
    MASS = "mass"
    VOLUME = "volume"
    COUNT = "count"


class Unit(StrEnum):
    MG = "mg"
    G = "g"
    KG = "kg"
    ML = "ml"
    L = "L"
    ITEM = "item"


class RecipeStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"


class StockOperation(StrEnum):
    ADD = "add"
    SET = "set"
    REMOVE = "remove"


class ProfileResponse(ApiModel):
    id: UUID
    display_name: str
    preferred_servings: int | None
    measurement_system: Literal["metric"]
    created_at: datetime
    updated_at: datetime


class ProfileUpdate(ApiModel):
    display_name: Name | None = None
    preferred_servings: int | None = Field(default=None, ge=1, le=1000)

    @model_validator(mode="after")
    def require_change(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("At least one profile field is required.")
        if "display_name" in self.model_fields_set and self.display_name is None:
            raise ValueError("Display name cannot be null.")
        return self


class CategoryCreate(ApiModel):
    id: UUID | None = None
    name: Name


class CategoryUpdate(ApiModel):
    name: Name


class CategoryResponse(ApiModel):
    id: UUID
    name: str
    scope: Scope
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class IngredientCreate(ApiModel):
    id: UUID | None = None
    name: Name
    category_id: UUID
    measurement_family: MeasurementFamily
    track_in_pantry: bool = False


class IngredientUpdate(ApiModel):
    name: Name | None = None
    category_id: UUID | None = None
    measurement_family: MeasurementFamily | None = None

    @model_validator(mode="after")
    def require_change(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("At least one ingredient field is required.")
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("Ingredient name cannot be null.")
        if "category_id" in self.model_fields_set and self.category_id is None:
            raise ValueError("Category cannot be null.")
        if "measurement_family" in self.model_fields_set and self.measurement_family is None:
            raise ValueError("Measurement family cannot be null.")
        return self


class IngredientResponse(ApiModel):
    id: UUID
    name: str
    category_id: UUID
    measurement_family: MeasurementFamily
    scope: Scope
    tracked_in_pantry: bool
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class IngredientPage(ApiModel):
    items: list[IngredientResponse]
    total: int
    limit: int
    offset: int


class PantryStockResponse(ApiModel):
    id: UUID
    ingredient_id: UUID
    quantity: SerializedDecimal
    created_at: datetime
    updated_at: datetime


class StockOperationCreate(ApiModel):
    event_id: UUID
    operation: StockOperation
    amount: Decimal = Field(max_digits=15, decimal_places=3)
    reason: str = Field(default="Manual adjustment", max_length=500)

    @model_validator(mode="after")
    def validate_amount(self) -> Self:
        if self.operation != StockOperation.SET and self.amount <= 0:
            raise ValueError("Add and remove amounts must be greater than zero.")
        return self


class RecipeIngredientWrite(ApiModel):
    ingredient_id: UUID
    quantity: str = Field(default="", max_length=64)
    unit: Unit


class RecipeCreate(ApiModel):
    id: UUID
    name: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=5000)
    base_servings: int = Field(ge=1, le=1000)
    ingredients: list[RecipeIngredientWrite] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=lambda: [""])


class RecipeUpdate(ApiModel):
    name: str = Field(max_length=120)
    description: str = Field(max_length=5000)
    base_servings: int = Field(ge=1, le=1000)
    ingredients: list[RecipeIngredientWrite]
    steps: list[str]


class RecipeIngredientResponse(ApiModel):
    ingredient_id: UUID
    quantity: SerializedDecimal | None
    quantity_input: str
    unit: Unit


class RecipeResponse(ApiModel):
    id: UUID
    status: RecipeStatus
    scope: Scope
    name: str
    description: str
    base_servings: int
    ingredients: list[RecipeIngredientResponse]
    steps: list[str]
    created_at: datetime
    updated_at: datetime


class CookRecipeCreate(ApiModel):
    event_id: UUID
    servings: int = Field(ge=1, le=1000)


class StockChangeResponse(ApiModel):
    ingredient_id: UUID
    before: SerializedDecimal
    delta: SerializedDecimal
    after: SerializedDecimal


class ActivityResponse(ApiModel):
    id: UUID
    type: Literal["cooking", "manual", "reversal"]
    title: str
    detail: str
    occurred_at: datetime
    recipe_id: UUID | None
    servings: int | None
    changes: list[StockChangeResponse]
    reversed_at: datetime | None
    reversal_of: UUID | None


class ReverseActivityCreate(ApiModel):
    event_id: UUID


class StateResponse(ApiModel):
    revision: int
    profile: ProfileResponse
    categories: list[CategoryResponse]
    ingredients: list[IngredientResponse]
    pantry_stocks: list[PantryStockResponse]
    recipes: list[RecipeResponse]
    activity: list[ActivityResponse]


class LocalImportRequest(ApiModel):
    import_id: UUID
    state: dict[str, object]


class ImportConflict(ApiModel):
    kind: str
    local_id: str
    message: str


class LocalImportResponse(ApiModel):
    revision: int
    id_map: dict[str, UUID]
    conflicts: list[ImportConflict]


class ErrorResponse(ApiModel):
    code: str
    message: str
    details: dict[str, object]
    request_id: UUID


class HealthResponse(ApiModel):
    status: Literal["ok"] = "ok"
