from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List

@dataclass
class ParsedIdentity:
    mac1: Optional[str]
    mac2: Optional[str]
    mac1_source: str
    start_time: Optional[datetime]
    end_time: Optional[datetime]
    test_duration_sec: Optional[float]
    product: str
    flow_file: Optional[str]
    package_name: Optional[str]
    tester_sn: Optional[str]
    fw_version: Optional[str]

@dataclass
class ParsedMeasurement:
    technology: str
    direction: str
    standard: str
    band: Optional[str]
    frequency_mhz: Optional[int]
    bandwidth: Optional[str]
    rate: Optional[str]
    antenna: Optional[str]
    step_num: Optional[int]
    step_name: Optional[str]
    metric_name: str
    value: float
    unit: Optional[str]
    limit_low: Optional[float]
    limit_high: Optional[float]
    passed: Optional[bool]

@dataclass
class ParsedFailure:
    step_num: Optional[int]
    step_name: Optional[str]
    message: Optional[str]
    category: str

@dataclass
class DataQualityWarning:
    issue_type: str
    description: str

@dataclass 
class ParsedResult:
    identity: ParsedIdentity
    result: str
    work_order: str
    failure: Optional[ParsedFailure]
    measurements: List[ParsedMeasurement]
    warnings: List[DataQualityWarning]
    raw_log: str
    file_hash: str
    source_file: str

    @property
    def mac1(self) -> Optional[str]:
        return self.identity.mac1

    @property
    def mac2(self) -> Optional[str]:
        return self.identity.mac2

    @property
    def mac1_source(self) -> str:
        return self.identity.mac1_source

    @property
    def start_time(self) -> Optional[datetime]:
        return self.identity.start_time

    @property
    def end_time(self) -> Optional[datetime]:
        return self.identity.end_time

    @property
    def test_duration_sec(self) -> Optional[float]:
        return self.identity.test_duration_sec

    @property
    def product(self) -> str:
        return self.identity.product

    @property
    def flow_file(self) -> Optional[str]:
        return self.identity.flow_file

    @property
    def package_name(self) -> Optional[str]:
        return self.identity.package_name

    @property
    def tester_sn(self) -> Optional[str]:
        return self.identity.tester_sn

    @property
    def fw_version(self) -> Optional[str]:
        return self.identity.fw_version

    @property
    def fail_step_num(self) -> Optional[int]:
        return self.failure.step_num if self.failure else None

    @property
    def fail_step_name(self) -> Optional[str]:
        return self.failure.step_name if self.failure else None

    @property
    def fail_message(self) -> Optional[str]:
        return self.failure.message if self.failure else None

    @property
    def fail_category(self) -> Optional[str]:
        return self.failure.category if self.failure else None

    @property
    def issues(self) -> List[DataQualityWarning]:
        return self.warnings
