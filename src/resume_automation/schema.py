import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class ProjectBlock(Contract):
    organization_name: str
    project_title: str
    descriptive_title: bool = Field(default=False, description="True when the title is a descriptive caption rather than an explicit project name")
    role: str
    duration: str = Field(default="", description="Exact project-specific duration, empty if absent")
    description: str = Field(default="", description="Explicit project overview if present in the source, otherwise empty")
    responsibilities: list[str] = Field(description="Verbatim bullets in source order")

    @field_validator("project_title")
    @classmethod
    def remove_title_label(cls, value: str) -> str:
        value = re.sub(r"^(?:name of the project|project(?: name| title)?|title)\s*:\s*",
                       "", value.strip(), flags=re.I)
        value = re.sub(r"(?:\s*[,|–—-]?\s*Client(?: Name)?\s*[:|].*)$", "", value, flags=re.I)
        return value.strip()


class CoforgeResumeSchema(Contract):
    full_name: str
    private_names: list[str] = Field(default_factory=list, description="Employer, client and engagement industry labels to omit from the displayed resume, including source aliases")
    email: str | None = None
    phone: str | None = None
    profile_summary: list[str]
    technical_skills: dict[str, list[str]]
    education: list[str] = Field(default_factory=list, description="Candidate degrees, institutions, and graduation years extracted verbatim from source")
    certifications: list[str] = Field(default_factory=list)
    projects: list[ProjectBlock] = Field(description="Newest to oldest when explicitly dated")
    missing_sections: list[str] = Field(default_factory=list)
    confidence_score: float = Field(default=1.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def separate_education(self):
        def key(value: str) -> str:
            return " ".join(value.casefold().split()).rstrip(".")

        education = {key(value) for value in self.education}
        if education:
            self.profile_summary = [item for item in self.profile_summary if key(item) not in education]
            self.certifications = [item for item in self.certifications if key(item) not in education]
        return self

    @field_validator("technical_skills")
    @classmethod
    def clean_skills(cls, categories: dict[str, list[str]]) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        seen: set[str] = set()
        for category, entries in categories.items():
            for entry in entries:
                for item in split_skills(entry):
                    key = item.casefold()
                    if key in seen or key in SOFT_SKILLS or re.match(
                        r"^(?:experienced in|proficient in|strong knowledge of|responsible for|ability to)\b", key
                    ):
                        continue
                    seen.add(key)
                    result.setdefault(category.strip(), []).append(item)
        return result


SOFT_SKILLS = {"communication", "communication skills", "teamwork", "team player",
               "leadership", "leadership skills", "problem solving", "problem-solving",
               "interpersonal skills", "time management", "collaboration"}


def split_skills(value: str):
    depth = start = 0
    for index, char in enumerate(value):
        if char in "([":
            depth += 1
        elif char in ")]":
            depth = max(0, depth - 1)
        elif not depth and char in ",;\n":
            item = value[start:index].strip(" \u2022\t")
            if item:
                yield item
            start = index + 1
    item = value[start:].strip(" \u2022\t")
    if item:
        yield item


class SkillCategory(Contract):
    category: str
    items: list[str]


def router_schema() -> dict:
    schema = CoforgeResumeSchema.model_json_schema()
    schema["$defs"]["SkillCategory"] = SkillCategory.model_json_schema()
    schema["properties"]["technical_skills"] = {
        "type": "array", "items": {"$ref": "#/$defs/SkillCategory"}
    }
    def close(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                node["additionalProperties"] = False
                node["required"] = list(node["properties"])
            for key in ("default", "minimum", "maximum"):
                node.pop(key, None)
            for child in node.values():
                close(child)
        elif isinstance(node, list):
            for child in node:
                close(child)
    close(schema)
    return schema
