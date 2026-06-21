"""
Case submission API routes.

Workers submit scraped cases in batches.
The orchestrator:
  1. Saves case metadata to PostgreSQL
  2. Generates presigned S3 URLs for any documents that need PDF upload
  3. Returns the URLs so the worker can upload PDF bytes directly to S3
"""

from fastapi import APIRouter, Depends, Request

from orchestrator.middleware.auth import verify_api_key
from orchestrator.models.api_schemas import (
    BatchCaseSubmission,
    BatchCaseResponse,
    CaseSubmission,
)
from orchestrator.services.s3_client import S3Client
from src.models.case import (
    Case,
    CaseDocument,
    CaseInstance,
    CaseParticipant,
    InstanceUpdate,
)
from src.storage.database import get_session
from src.storage.repository import CaseRepository
from src.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/cases", tags=["cases"])


def _submission_to_case(submission: CaseSubmission) -> Case:
    """Convert a CaseSubmission to a Pydantic Case model."""
    # Rebuild participants from flat list → dict of lists
    participants_dict: dict[str, list[CaseParticipant]] = {}
    for p in submission.participants:
        role = p.get("role", "other_party")
        participants_dict.setdefault(role, []).append(
            CaseParticipant(
                name=p.get("name", ""),
                role=role,
                inn=p.get("inn"),
                address=p.get("address"),
                ogrn=p.get("ogrn"),
            )
        )

    # Rebuild instances with documents and updates
    instances = []
    for inst in submission.instances:
        docs = []
        for d in inst.get("documents", []):
            docs.append(
                CaseDocument(
                    id=d.get("id"),
                    filename=d.get("filename"),
                    url=d.get("url"),
                    type=d.get("type"),
                    date=d.get("date"),
                    priority=d.get("priority"),
                    publish_date=d.get("publish_date"),
                    extracted_text=d.get("extracted_text"),
                    storage_key=d.get("storage_key"),
                )
            )
        updates = []
        for u in inst.get("updates", []):
            updates.append(
                InstanceUpdate(
                    date=u.get("date"),
                    type=u.get("type"),
                    subject=u.get("subject"),
                    content=u.get("content"),
                    pdf_url=u.get("pdf_url"),
                    pdf_publish_date=u.get("pdf_publish_date"),
                    additional_info=u.get("additional_info"),
                    judge_panel=u.get("judge_panel"),
                    reporting_judge=u.get("reporting_judge"),
                )
            )
        instances.append(
            CaseInstance(
                court_name=inst.get("court_name", ""),
                instance_level=inst.get("instance_level"),
                case_number=inst.get("case_number"),
                incoming_number=inst.get("incoming_number"),
                date=inst.get("date"),
                result_text=inst.get("result_text"),
                result_pdf_url=inst.get("result_pdf_url"),
                updates=updates,
                documents=docs,
            )
        )

    return Case(
        id=submission.id,
        case_number=submission.case_number,
        court=submission.court,
        case_url=submission.case_url,
        case_type=submission.case_type,
        current_instance=submission.current_instance,
        is_simple_justice=submission.is_simple_justice,
        filing_date=submission.filing_date,
        judges=submission.judges,
        participants=participants_dict,
        instances=instances,
        category=submission.category,
        relevance_score=submission.relevance_score,
        status=submission.status,
        extracted_data=submission.extracted_data,
        case_status_text=submission.case_status_text,
        case_category_text=submission.case_category_text,
        claim_amount=submission.claim_amount,
        case_page_scraped=submission.case_page_scraped,
        raw_html=submission.raw_html,
        pdf_texts=submission.pdf_texts,
    )


@router.post("/batch", response_model=BatchCaseResponse)
async def submit_cases_batch(
    request: Request,
    body: BatchCaseSubmission,
    _: str = Depends(verify_api_key),
):
    """
    Submit a batch of scraped cases.

    Workers call this after scraping a judge. Cases are upserted.
    The orchestrator returns presigned S3 URLs for any documents
    that the worker has PDF bytes for.
    """
    session = get_session()
    try:
        repo = CaseRepository(session)
        saved = 0
        errors = 0

        for case_sub in body.cases:
            try:
                case = _submission_to_case(case_sub)
                repo.save_case(case)
                saved += 1
            except Exception as e:
                logger.error("Error saving case %s: %s", case_sub.id, e)
                errors += 1

        logger.info(
            "Batch from worker %s for judge %s: %d saved, %d errors",
            body.worker_id,
            body.judge_name,
            saved,
            errors,
        )

        # Generate presigned S3 URLs for document uploads
        upload_urls: dict[str, dict[str, str]] = {}
        s3_client: S3Client = request.app.state.s3_client

        if s3_client.is_configured() and body.documents:
            for doc in body.documents:
                case_id = doc.get("case_id")
                doc_id = doc.get("doc_id")
                filename = doc.get("filename")

                if not case_id or not filename:
                    continue

                try:
                    url = s3_client.generate_presigned_upload_url(
                        case_id=case_id,
                        doc_id=doc_id or "doc",
                        filename=filename,
                        expiry_seconds=900,
                    )
                    upload_urls.setdefault(case_id, {})[doc_id or filename] = url
                except Exception as e:
                    logger.warning(
                        "Failed to generate presigned URL for %s/%s: %s",
                        case_id,
                        filename,
                        e,
                    )

        return BatchCaseResponse(
            ok=True,
            message=f"Batch processed: {saved} saved, {errors} errors",
            saved=saved,
            errors=errors,
            upload_urls=upload_urls,
        )
    finally:
        session.close()
