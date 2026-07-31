from typing import Literal, Optional

from pydantic import BaseModel, Field


class AssetIn(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""
    link: str = ""
    hashtags: str = ""


class BatchRequestIn(BaseModel):
    asset_id: int
    outro_id: int
    cut_seconds: float = Field(gt=0)
    source_type: Literal["upload", "url"]
    source_url: Optional[str] = None
    file_key: Optional[str] = None
