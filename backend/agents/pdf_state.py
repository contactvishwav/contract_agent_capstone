from typing import TypedDict, Optional
from backend.domain.value_objects import ProcessingResult, ContractData

class PDFProcessingState(TypedDict):
    file_path: str
    tenant_id: str
    extracted_text: Optional[str]
    contract_data: Optional[ContractData]
    processing_result: Optional[ProcessingResult]
    messages: list
    filename: Optional[str]