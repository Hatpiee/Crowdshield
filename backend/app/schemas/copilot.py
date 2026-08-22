from pydantic import BaseModel, Field


class CopilotQuestionRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)


class CopilotAnswerRead(BaseModel):
    answer: str
    cited_timestamps: list[float]


class CopilotSuggestedQuestionsRead(BaseModel):
    questions: list[str]
