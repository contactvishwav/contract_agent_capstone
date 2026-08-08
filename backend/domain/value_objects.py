from dataclasses import dataclass
from typing import Optional, List
from enum import Enum

class ProcessingStatus(Enum):
    SUCCESS = "success"
    ERROR = "error"
    SKIPPED = "skipped"
    REVIEW_REQUIRED = "review_required"

@dataclass
class ProcessingResult:
    status: ProcessingStatus
    contract_id: Optional[str] = None
    message: str = ""
    error: Optional[str] = None

@dataclass
class ContractData:
    is_contract: bool
    confidence_score: float
    contract_type: str
    summary: str
    parties: List[dict]
    effective_date: Optional[str] = None
    end_date: Optional[str] = None
    total_amount: Optional[float] = None
    governing_law: Optional[str] = None
    key_terms: List[str] = None
    
    def __post_init__(self):
        if self.key_terms is None:
            self.key_terms = []
    full_text: str = ""
    # Original uploaded filename - threaded through so the repository can
    # store it on the Contract node and duplicate-detection can actually
    # match against it (see contract_repository.py's store_contract). A
    # real, confirmed bug found live: Contract.file_id is a random
    # UUID-based id (contract_repository.py's
    # f"UPLOADED_{uuid4().hex[:8]}_{date}"), never derived from the
    # filename, so document_upload.py's duplicate check ("WHERE c.file_id
    # CONTAINS $filename") could structurally never match any real
    # Contract - uploading the exact same file twice silently created two
    # unrelated contracts every time, live-reproduced during verification
    # of the duplicate-response-shape fix in the same file.
    filename: Optional[str] = None