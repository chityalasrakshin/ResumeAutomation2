from pydantic import BaseModel, ConfigDict, Field


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class ProjectBlock(Contract):
    organization_name: str
    project_title: str
    role: str
    duration: str = Field(default="", description="Exact project-specific duration, empty if absent")
    description: str
    responsibilities: list[str] = Field(description="Verbatim bullets in source order")


class CoforgeResumeSchema(Contract):
    full_name: str
    email: str | None = None
    phone: str | None = None
    profile_summary: list[str]
    technical_skills: dict[str, list[str]]
    certifications: list[str] = Field(default_factory=list)
    projects: list[ProjectBlock] = Field(description="Newest to oldest when explicitly dated")
    missing_sections: list[str] = Field(default_factory=list)
    confidence_score: float = Field(default=1.0, ge=0.0, le=1.0)
