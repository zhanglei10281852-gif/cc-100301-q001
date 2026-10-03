from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum


class Gender(str, Enum):
    male = "男"
    female = "女"


class AffairCategory(str, Enum):
    huji = "户籍"
    shebao = "社保"
    yibao = "医保"
    dibao = "低保"
    jianfang = "建房"
    jisheng = "计生"
    other = "其他"


class AffairStatus(str, Enum):
    pending = "待受理"
    processing = "办理中"
    completed = "已办结"
    rejected = "已退回"


class AnnouncementCategory(str, Enum):
    notice = "通知"
    announcement = "公告"
    policy = "政策"
    publicity = "公示"


class ResidentCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)
    id_card: str = Field(..., min_length=18, max_length=18)
    gender: Gender
    birth_date: str
    phone: Optional[str] = None
    address: str
    village: str
    household_head: Optional[str] = None


class ResidentUpdate(BaseModel):
    phone: Optional[str] = None
    address: Optional[str] = None
    village: Optional[str] = None
    household_head: Optional[str] = None


class AffairCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    category: AffairCategory
    applicant_id: int
    description: Optional[str] = None


class AffairProcess(BaseModel):
    status: AffairStatus
    department_id: Optional[int] = None
    handler: str
    result: Optional[str] = None


class AnnouncementCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    content: str
    category: AnnouncementCategory
    publisher: str
    is_pinned: bool = False


class PetitionType(str, Enum):
    complaint = "投诉举报"
    suggestion = "意见建议"
    help = "求助咨询"
    info_apply = "信息公开申请"


class PetitionStatus(str, Enum):
    pending_receipt = "待签收"
    pending_assign = "待分派"
    processing = "办理中"
    pending_review = "待审核"
    completed = "已办结"
    returned = "退回重办"
    review_requested = "复查中"
    review_completed = "复查完结"


class TimeoutStatus(str, Enum):
    normal = "正常"
    warning = "即将超期"
    expired = "已超期"


class DepartmentCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    manager: str = Field(..., max_length=50)
    phone: str = Field(..., max_length=20)


class DepartmentUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    manager: Optional[str] = Field(None, max_length=50)
    phone: Optional[str] = Field(None, max_length=20)


class PetitionCreate(BaseModel):
    type: PetitionType
    target: str = Field(..., max_length=200)
    content: str
    demand: Optional[str] = None
    contact: Optional[str] = None
    is_anonymous: bool = False


class PetitionAssign(BaseModel):
    department_id: int
    deadline_days: int = Field(..., gt=0)


class PetitionProcess(BaseModel):
    result: str


class PetitionReview(BaseModel):
    passed: bool
    review_opinion: Optional[str] = None


class PetitionReapplyReview(BaseModel):
    reason: str


class PetitionUrgeCreate(BaseModel):
    reason: str
    operator: str = Field(..., max_length=50)
