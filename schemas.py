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
        return max(
            0,
            min(float(value), 60),
        )


class ScoreWeights(BaseModel):
    skills: float = 0.50
    experience: float = 0.25
    education: float = 0.15
    projects: float = 0.10

    def normalized(self):
        total = (
            self.skills
            + self.experience
            + self.education
            + self.projects
        )

        if total <= 0:
            return ScoreWeights()

        return ScoreWeights(
            skills=self.skills / total,
            experience=(
                self.experience / total
            ),
            education=(
                self.education / total
            ),
            projects=(
                self.projects / total
            ),
        )


class ScoreResult(BaseModel):
    candidate_code: str
    total_score: float

    skill_score: float
    experience_score: float
    education_score: float
    project_score: float

    matched_skills: list[str]
    missing_skills: list[str]

    evidence: list[str]
    uncertainties: list[str]

    recommendation: str = (
        "仅供HR人工复核，"
        "不得作为自动录用或淘汰决定。"
    )


class ChatRequest(BaseModel):
    session_id: str
    hr_id: str
    message: str
    jd: str


class DeleteRequest(BaseModel):
    candidate_code: str


class DeleteConfirmRequest(BaseModel):
    token: str
    confirmed: boolr