"""Reports module"""

from app.core.reports.report_generator import (
    generate_pptx,
    generate_excel,
    generate_pdf,
    generate_docx,
)
from app.core.reports.meeting_memo import (
    generate_meeting_memo,
    generate_meeting_memo_pptx,
    generate_meeting_memo_html,
    generate_meeting_memo_report,
)
from app.core.reports.officecli_service import (
    OfficeCLIService,
    get_officecli_service,
)

__all__ = [
    "generate_pptx",
    "generate_excel",
    "generate_pdf",
    "generate_docx",
    "generate_meeting_memo",
    "generate_meeting_memo_pptx",
    "generate_meeting_memo_html",
    "generate_meeting_memo_report",
    "OfficeCLIService",
    "get_officecli_service",
]
