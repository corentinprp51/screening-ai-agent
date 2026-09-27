"""Domain models: candidate state, the extraction contract and client config."""

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Literal, get_args

from pydantic import BaseModel, BeforeValidator, Field, JsonValue, model_validator

Language = Literal["es", "en"]
FieldType = Literal[
    "name",
    "license",
    "own_vehicle",
    "service_area",
    "availability",
    "schedule",
    "experience",
    "start_date",
]
# How the reply is phrased around its action, chosen by code (the action is unchanged).
# `resuming`: the candidate writes back after a Nudge or after Abandoned.
Cue = Literal["resuming"]
AvailabilityOption = Literal["full_time", "part_time", "weekends"]
ScheduleOption = Literal["morning", "afternoon", "evening", "flexible"]
VehicleType = Literal["car", "moped_motorcycle"]


class License(BaseModel):
    """`expired` or `pending` gets one follow-up; still not valid, it counts as a no."""

    has_license: bool | Literal["expired", "pending"]
    type: VehicleType | None = None

    @model_validator(mode="before")
    @classmethod
    def _from_answer(cls, data: JsonValue) -> JsonValue:
        """A bare answer is the yes, no, expired or pending ("yes" → has a license)."""
        return {"has_license": data} if isinstance(data, bool | str) else data


class OwnVehicle(BaseModel):
    """`shared` is a shared or borrowed vehicle: it leaves the knock-out undecided."""

    owns_vehicle: bool | Literal["shared"]
    type: VehicleType | None = None

    @model_validator(mode="before")
    @classmethod
    def _from_answer(cls, data: JsonValue) -> JsonValue:
        """A bare answer is the yes, no or shared ("no" → no own vehicle)."""
        return {"owns_vehicle": data} if isinstance(data, bool | str) else data


class Experience(BaseModel):
    years: int
    platforms: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _from_years(cls, data: JsonValue) -> JsonValue:
        """A bare number is the years ("2" → 2 years)."""
        return {"years": data} if isinstance(data, int | str) else data


class Place(BaseModel):
    """A place as the candidate said it: the city and the zone (a district or a town
    around it) in their own words. Matching it to the service areas is left to code."""

    city: str | None = None
    zone: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _from_text(cls, data: JsonValue) -> JsonValue:
        """A bare text is the city ("Getafe" → city "Getafe"); code also tries it as a zone."""
        return {"city": data} if isinstance(data, str) else data


class Location(BaseModel):
    """A place matched against the client's service areas. Country and city are the
    config's names when matched; outside the areas, the city is kept as said. An unknown
    place (no city yet) has no city."""

    country: str | None = None
    city: str | None = None
    zone: str | None = None
    in_service_area: bool


# The value types a field can hold. A start date is stored as "immediate" or an ISO date.
FieldValue = str | list[str] | Experience | License | OwnVehicle | Location
FieldStatus = Literal["empty", "incomplete", "valid", "needs_review"]


class Status(StrEnum):
    IN_PROGRESS = "in_progress"
    REJECTION_PROPOSED = "rejection_proposed"
    QUALIFIED = "qualified"
    QUALIFIED_TO_REVIEW = "qualified_to_review"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    ABANDONED = "abandoned"


# --- Client config (loaded from YAML by the config adapter) ---


class Persona(BaseModel):
    agent_name: str
    client_name: str


class FieldConfig(BaseModel):
    type: FieldType
    knock_out: bool = False


class Templates(BaseModel):
    greeting: dict[Language, str]
    fallback: dict[Language, str]
    after_close: dict[Language, str]
    nudges: dict[Language, list[str]]  # one per nudge delay, in order


class ScoreWeights(BaseModel):
    """The most points each field can bring to the priority score."""

    availability: int = Field(ge=0)
    schedule: int = Field(ge=0)
    start_date: int = Field(ge=0)
    experience: int = Field(ge=0)

    @model_validator(mode="after")
    def _add_up_to_100(self) -> "ScoreWeights":
        if sum(self.model_dump().values()) != 100:
            raise ValueError("The score weights must add up to 100")
        return self


class OpenShifts(BaseModel):
    """The availability and schedules the client is currently hiring for."""

    availability: list[AvailabilityOption] = Field(min_length=1)
    schedule: list[ScheduleOption] = Field(min_length=1)


class Scoring(BaseModel):
    weights: ScoreWeights
    open_shifts: OpenShifts


# Country → city → its zones (possibly none).
ServiceAreas = dict[str, dict[str, list[str]]]


class ClientConfig(BaseModel):
    client_id: str
    persona: Persona
    default_language: Language
    fields: list[FieldConfig] = Field(min_length=1)
    review_delay_hours: int = Field(gt=0)  # a recruiter replies to a proposed rejection within
    call_within_hours: int = Field(gt=0)  # a recruiter calls a qualified candidate within
    confidence_threshold: float = Field(ge=0, le=1)  # below it, a value is confirmed first
    nudge_delays_hours: list[int] = Field(min_length=1)  # after the last unanswered question
    deadline_hours: int = Field(gt=0)  # after it, a silent screening ends
    service_areas: ServiceAreas = Field(min_length=1)
    platforms: list[str] = Field(min_length=1)  # known delivery platforms, anything else is other
    scoring: Scoring
    templates: Templates

    @model_validator(mode="after")
    def _one_nudge_per_delay(self) -> "ClientConfig":
        delays = self.nudge_delays_hours
        if delays != sorted(set(delays)) or delays[0] <= 0 or delays[-1] >= self.deadline_hours:
            raise ValueError("The nudge delays must increase, from above 0 to below the deadline")
        if set(self.templates.nudges) != set(get_args(Language)):
            raise ValueError("The nudges need templates in every language")
        for language, nudges in self.templates.nudges.items():
            if len(nudges) != len(delays):
                raise ValueError(f"The {language} nudges need one template per nudge delay")
        return self

    def greeting(self, language: Language) -> str:
        return self.templates.greeting[language].format(
            agent_name=self.persona.agent_name, client_name=self.persona.client_name
        )

    def nudge(
        self, language: Language, number: int, first_name: str | None, questions_left: int
    ) -> str:
        """The nudge template `number` (from 1). `{name}` is ", <first name>", or empty
        while the name is unknown."""
        return self.templates.nudges[language][number - 1].format(
            name=f", {first_name}" if first_name else "", questions_left=questions_left
        )


# --- Candidate state (stored as state_json) ---


class FieldState(BaseModel):
    """What the screening knows about one field.

    `incomplete` means one follow-up is pending for the `missing` part.
    `needs_review` means a recruiter must check it; the screening moves on.
    `unconfirmed` is a value waiting for the candidate's confirmation (unsure, or a correction
    of a valid value); it replaces this state on a yes.
    """

    status: FieldStatus = "empty"
    value: FieldValue | None = None
    raw_answer: str | None = None
    confidence: float | None = None
    missing: str | None = None
    attempts: int = 0
    flags: list[str] = Field(default_factory=list)
    unconfirmed: "FieldState | None" = None


class CandidateState(BaseModel):
    consent: bool | None = None
    opted_out: bool = False
    overridden_knock_outs: list[str] = Field(default_factory=list)  # rules set aside
    fields: dict[str, FieldState] = Field(default_factory=dict)
    recap_confirmed: bool = False
    recap_attempts: int = 0  # recap answers that were neither a yes nor a correction
    language: Language = "es"
    stage: str = "consent"
    flags: list[str] = Field(default_factory=list)  # on the candidate, not a field

    def field(self, field_type: str) -> FieldState:
        return self.fields.get(field_type, FieldState())

    def add_flag(self, flag: str) -> None:
        if flag not in self.flags:
            self.flags.append(flag)

    def all_flags(self) -> list[str]:
        return self.flags + [flag for field in self.fields.values() for flag in field.flags]


class Score(BaseModel):
    """The priority score, with the points each field brought (the breakdown)."""

    points: dict[str, int] = Field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.points.values())


class Summary(BaseModel):
    """The recruiter summary and the facts code gave the LLM to phrase it. `text` is None
    when the LLM call failed."""

    text: str | None = None
    facts: dict[str, JsonValue]


class Candidate(BaseModel):
    id: int | None = None
    client_id: str
    handle: str
    name: str | None = None
    city: str | None = None
    status: Status = Status.IN_PROGRESS
    state: CandidateState = Field(default_factory=CandidateState)
    score: Score = Field(default_factory=Score)
    summary: Summary | None = None  # written when the questions stop
    created_at: datetime
    updated_at: datetime


class Message(BaseModel):
    role: Literal["candidate", "agent"]
    content: str
    language: Language
    created_at: datetime


class Event(BaseModel):
    type: str
    stage: str
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    created_at: datetime


# --- Extraction contract (the LLM output type) ---


def _split_words(value: JsonValue) -> JsonValue:
    """Several options in one string ("full_time, weekends") → a list."""
    return value.replace(",", " ").split() if isinstance(value, str) else value


Availability = Annotated[list[AvailabilityOption], BeforeValidator(_split_words)]


class Extracted[T](BaseModel):
    value: T
    raw_answer: str
    confidence: float = Field(ge=0, le=1)


class Extraction(BaseModel):
    """Everything the LLM may understand from one candidate message."""

    language: Language = "es"
    intent: Literal["answer", "opt_out"] = "answer"
    yes_no: bool | None = None
    name: Extracted[str] | None = None
    license: Extracted[License] | None = None
    own_vehicle: Extracted[OwnVehicle] | None = None
    service_area: Extracted[Place] | None = None
    availability: Extracted[Availability] | None = None
    schedule: Extracted[ScheduleOption] | None = None
    experience: Extracted[Experience] | None = None
    start_date: Extracted[Literal["immediate"] | date] | None = None
