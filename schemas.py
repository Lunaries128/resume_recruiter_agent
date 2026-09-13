from pydantic import (
    BaseModel,
    Field,
    field_validator,
)


class EducationItem(BaseModel):
    school: str = ""
    degree: str = ""
    major: str = ""


class ProjectItem(BaseModel):
    name: str = ""
    description: str = ""

    technologies: list[str] = Field(
        default_factory=list
    )


class CandidateProfile(BaseModel):
    candidate_code: str = ""

    education: list[
        EducationItem
    ] = Field(
        default_factory=list
    )

    skills: list[str] = Field(
        default_factory=list
    )

    work_years: float = 0

    job_titles: list[str] = Field(
        default_factory=list
    )

    companies: list[str] = Field(
        default_factory=list
    )

    projects: list[
        ProjectItem
    ] = Field(
        default_factory=list
    )

    certificates: list[str] = Field(
        default_factory=list
    )

    summary: str = ""

    missing_fields: list[str] = Field(
        default_factory=list
    )

    @field_validator("work_years")
    @classmethod
    def validate_work_years(
        cls,
        value,
    ):
        try:
            number = float(value)

        except (
            TypeError,
            ValueError,
        ):
            return 0

        return max(
            0,
            min(number, 60),
        )


class ScoreWeights(BaseModel):
    skills: float = 0.50
    experience: float = 0.25
    education: float = 0.15
    projects: float = 0.10

    def normalized(self):
        values = [
            max(self.skills, 0),
            max(self.experience, 0),
            max(self.education, 0),
            max(self.projects, 0),
        ]

        total = sum(values)

        if total <= 0:
            return ScoreWeights()

        return ScoreWeights(
            skills=values[0] / total,
            experience=values[1] / total,
            education=values[2] / total,
            projects=values[3] / total,
        )


class ScoreResult(BaseModel):
    candidate_code: str
    total_score: float

    skill_score: float
    experience_score: float
    education_score: float
    project_score: float

    matched_skills: list[str] = Field(
        default_factory=list
    )

    missing_skills: list[str] = Field(
        default_factory=list
    )

    matched_projects: list[str] = Field(
        default_factory=list
    )

    evidence: list[str] = Field(
        default_factory=list
    )

    uncertainties: list[str] = Field(
        default_factory=list
    )

    recommendation: str = (
        "仅供HR人工复核，"
        "不得作为自动录用或淘汰决定。"
    )


class ChatRequest(BaseModel):
    session_id: str
    hr_id: str = "default_hr"
    message: str
    jd: str = ""


class DeleteRequest(BaseModel):
    candidate_code: str


class DeleteConfirmRequest(BaseModel):
    token: str
    confirmed: bool