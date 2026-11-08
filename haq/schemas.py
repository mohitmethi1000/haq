"""Data passed between steps. There is deliberately no field for court cases."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

DisputeType = Literal["refund", "salary", "deposit", "notice", "other"]


class TimelineEvent(BaseModel):
    date: Optional[str] = Field(None, description="YYYY-MM-DD if known, else null")
    event: str
    user_words: Optional[str] = Field(None, description="short phrase from the user's own message that supports this")


class Facts(BaseModel):
    """What the model extracts from the story. Unknown = null. The user confirms this."""
    dispute_type: DisputeType = "other"
    user_language: str = Field("english", description="english | hindi (Devanagari) | hinglish (Roman Hindi mixed with English) | roman_hindi (Hindi in Roman letters, little English) | tamil | bengali | marathi | telugu | kannada | gujarati | other: the language the person WROTE in")
    user_state: Optional[str] = Field(None, description="Indian State/UT where the user lives or works")
    user_city: Optional[str] = None
    other_party_name: Optional[str] = None
    other_party_kind: Literal["company", "online_platform", "individual", "government", "unknown"] = "unknown"
    amount_claimed_inr: Optional[float] = None
    amount_paid_inr: Optional[float] = Field(None, description="refund cases: price paid for the goods/service")
    purchase_date: Optional[str] = None
    refund_refused_date: Optional[str] = Field(None, description="FIRST date the seller clearly refused the refund (not a return date, not a later promise); null if never clearly refused")
    bought_for_business: Optional[bool] = None
    self_employed_livelihood: Optional[bool] = Field(None, description="true if the person uses the thing THEMSELVES to earn their own living by self-employment (e.g. drives their own auto-rickshaw)")
    work_relationship: Literal["employee", "freelance_or_contract", "gig_platform", "domestic_worker", "government_employee", "agency_worker", "unknown"] = Field(
        "unknown", description="salary cases: how the person worked. freelance_or_contract = paid per invoice/project; gig_platform = delivery/ride app")
    employment_end: Literal["still_employed", "resigned", "terminated", "unknown"] = "unknown"
    employment_end_date: Optional[str] = None
    unpaid_from_month: Optional[str] = Field(None, description="YYYY-MM of first unpaid month")
    unpaid_to_month: Optional[str] = Field(None, description="YYYY-MM of last unpaid month")
    monthly_wage_inr: Optional[float] = None
    pf_deducted_not_deposited: Optional[bool] = None
    deposit_paid_date: Optional[str] = None
    moved_out_date: Optional[str] = None
    notice_text_summary: Optional[str] = Field(None, description="if the user received a notice/call: what it demands")
    timeline: list[TimelineEvent] = []
    evidence: list[str] = Field([], description="proof the user says they have: receipts, chats, salary slips...")
    red_flags: list[str] = Field([], description="threats, arrest, violence, minor, police, self-harm, demands for money transfer")
    user_goal: Optional[str] = None
    missing: list[str] = Field([], description="important facts still unknown")

    @field_validator("*", mode="before")
    @classmethod
    def _null_strings(cls, v):
        """The model sometimes writes the string "null" instead of null."""
        if isinstance(v, str) and v.strip().lower() in ("null", "none", "n/a", "na", "unknown date", ""):
            return None
        return v


class Claim(BaseModel):
    text: str = Field(description="one plain-English sentence")
    cite: list[str] = Field([], description="section IDs from the provided pack ONLY, e.g. CPA2019_s69")


class Step(BaseModel):
    action: str
    why: str
    cite: list[str] = []


class Answer(BaseModel):
    """What the model writes after the rules have run. English first; rendered later."""
    summary: str = Field(description="2-3 plain sentences: where the person stands")
    claims: list[Claim] = []
    next_steps: list[Step] = []
    questions_for_user: list[str] = []
    not_covered: list[str] = Field([], description="things the pack does not cover; say so instead of guessing")
    corrections: list[str] = Field([], description="if the user believed something wrong (e.g. an old law), correct it here")


class Rendered(BaseModel):
    summary: str
    claims: list[str]
    next_steps: list[str]
    questions_for_user: list[str] = []
