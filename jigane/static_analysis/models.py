"""SARIF の入力の型と、正規化した指摘の型。"""

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Level = Literal["error", "warning", "note", "none"]

# 知らない欄は読み捨て、使う欄の型が違えば拒む。数字の文字列などを型変換で通さない
_SARIF = ConfigDict(extra="ignore", strict=True, frozen=True)


class _SarifModel(BaseModel):
    model_config = _SARIF


class SarifMessage(_SarifModel):
    text: str


class SarifArtifactLocation(_SarifModel):
    uri: str | None = None


class SarifRegion(_SarifModel):
    start_line: int | None = Field(default=None, alias="startLine", ge=1)
    end_line: int | None = Field(default=None, alias="endLine", ge=1)


class SarifPhysicalLocation(_SarifModel):
    artifact_location: SarifArtifactLocation | None = Field(default=None, alias="artifactLocation")
    region: SarifRegion | None = None


class SarifLocation(_SarifModel):
    physical_location: SarifPhysicalLocation | None = Field(default=None, alias="physicalLocation")


class SarifResult(_SarifModel):
    rule_id: str | None = Field(default=None, alias="ruleId")
    level: Level | None = None
    message: SarifMessage
    locations: list[SarifLocation] | None = None

    @field_validator("locations", mode="before")
    @classmethod
    def _first_location_only(cls, value: Any) -> Any:  # noqa: ANN401 -- 検証前の値は何でも来る
        # 2 番目以降の位置は使わないので、型も確かめない
        return value[:1] if isinstance(value, list) else value


class SarifDriver(_SarifModel):
    name: str
    version: str | None = None


class SarifTool(_SarifModel):
    driver: SarifDriver


class SarifProvenance(_SarifModel):
    revision_id: str | None = Field(default=None, alias="revisionId")


class SarifRun(_SarifModel):
    tool: SarifTool
    results: list[SarifResult] | None = None
    version_control_provenance: list[SarifProvenance] | None = Field(
        default=None, alias="versionControlProvenance"
    )


class SarifLog(_SarifModel):
    version: Literal["2.1.0"]
    runs: list[SarifRun]


class DropReason(StrEnum):
    """指摘を捨てた理由。値は表示にそのまま使う。"""

    NO_LOCATION = "no_location"
    UNSUPPORTED_SCHEME = "unsupported_scheme"
    MALFORMED_URI = "malformed_uri"
    NUL_CHARACTER = "nul_character"
    CONTROL_CHARACTER = "control_character"
    FILE_URI_WITH_HOST = "file_uri_with_host"
    OUTSIDE_ANALYSIS_ROOT = "outside_analysis_root"
    DRIVE_LETTER = "drive_letter"
    UNC_PATH = "unc_path"
    ABSOLUTE_PATH = "absolute_path"
    PARENT_REFERENCE = "parent_reference"
    NOT_A_FILE = "not_a_file"
    OUTSIDE_REPOSITORY = "outside_repository"


class StaticFinding(BaseModel):
    """正規化した指摘一件。path はリポジトリの根からの posix の相対パス。"""

    model_config = ConfigDict(frozen=True)

    id: str
    tool_name: str
    tool_version: str
    rule_id: str
    level: Level
    message: str
    path: str
    start_line: int
    end_line: int


class SarifFileRecord(BaseModel):
    """読んだファイル一つの記録。revision_ids は照合せずに、書かれたまま順に持つ。"""

    model_config = ConfigDict(frozen=True)

    path: str
    sha256: str
    revision_ids: tuple[str, ...]


class SarifImport(BaseModel):
    model_config = ConfigDict(frozen=True)

    findings: tuple[StaticFinding, ...]
    dropped: dict[DropReason, int]
    files: tuple[SarifFileRecord, ...]
