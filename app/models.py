from typing import Optional

from pydantic import BaseModel


class LoginPayload(BaseModel):
    username: str
    password: str


class SetupPayload(BaseModel):
    username: str
    password: str


class RegisterPayload(BaseModel):
    username: str
    password: str


class InviteCreatePayload(BaseModel):
    expires_hours: Optional[int] = None


class UserUpdatePayload(BaseModel):
    role: Optional[str] = None
    quota_bytes: Optional[int] = None
    is_disabled: Optional[bool] = None


class UserDeletePayload(BaseModel):
    delete_files: bool = False


class MkdirPayload(BaseModel):
    path: str


class RenamePayload(BaseModel):
    path: str
    new_name: str


class MovePayload(BaseModel):
    paths: list[str]
    destination: str


class DeletePayload(BaseModel):
    paths: list[str]


class TrashPayload(BaseModel):
    ids: list[str]


class ShareCreatePayload(BaseModel):
    path: str
    password: Optional[str] = None
    expires_at: Optional[str] = None
    type: str = "file_download"


class SharePasswordPayload(BaseModel):
    password: str


class FileItem(BaseModel):
    name: str
    path: str
    is_dir: bool
    size: int = 0
    modified: str = ""
    mime_type: str = ""
    has_thumb: bool = False
