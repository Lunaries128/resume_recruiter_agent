from typing import Literal
from pydantic import BaseModel, Field


class EducationItem(BaseModel):
    school: str = ""
    degree: str = ""
    major: str = ""
    start_date: str = ""
    end_date: str = ""


class ExperienceItem(BaseModel):
    organization: str = ""
    role: str = ""
    start_date: str = ""
    end_date: str = ""
    actions: list[str] = Field(default_factory=list)
    results: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)


class ProjectItem(BaseModel):
    name: str = ""
    description: str = ""
    start_date: str = ""
    end_date: str = ""
    actions: list[str] = Field(default_factory=list)
    results: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)


class AwardItem(BaseModel):
    name: str = ""
    level: str = ""
    date: str = ""


class CandidateProfile(BaseModel):
    candidate_code: str = ""
    masked_name: str = "候选人*"
    masked_phone: str = ""
    email: str = ""
    education: list[EducationItem] = Field(default_factory=list)
    experiences: list[ExperienceItem] = Field(default_factory=list)
    projects: list[ProjectItem] = Field(default_factory=list)
    awards: list[AwardItem] = Field(default_factory=list)
    certificates: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    work_years: float = Field(default=0, ge=0, le=60)
    summary: str = ""
    missing_fields: list[str] = Field(default_factory=list)


class MatchDimension(BaseModel):
    key: str
    name: str
    score: float = Field(ge=0, le=100)
    weight: float = Field(ge=0, le=1)
    status: Literal['satisfied', 'partial', 'unsatisfied']
    requirement: str = ""
    candidate_value: str = ""
    evidence_source: str = ""
    evidence: list[str] = Field(default_factory=list)


class ScoreResult(BaseModel):
    candidate_code: str
    total_score: float = Field(ge=0, le=100)
    dimensions: list[MatchDimension] = Field(default_factory=list)
    satisfied: list[str] = Field(default_factory=list)
    partial: list[str] = Field(default_factory=list)
    unsatisfied: list[str] = Field(default_factory=list)
    audit_log: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)


class JobRequirements(BaseModel):
    minimum_education: str = ""
    required_majors: list[str] = Field(default_factory=list)
    minimum_work_years: float = Field(default=0, ge=0, le=60)
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    project_keywords: list[str] = Field(default_factory=list)
    award_keywords: list[str] = Field(default_factory=list)


class SessionEdit(BaseModel):
    title: str | None = Field(default=None, max_length=80)
    pinned: bool | None = None
    jd: str | None = Field(default=None, max_length=20000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=10000)
    hr_id: str = "default_hr"


class ScoreAllRequest(BaseModel):
    jd: str = Field(min_length=1, max_length=20000)


class DeleteRequest(BaseModel):
    kind: Literal['session', 'uploads']
    targets: list[str] = Field(default_factory=list)


class DeleteConfirmRequest(BaseModel):
    token: str
    confirmed: bool
