import pytest
from sqlalchemy.orm import Session
from core.models.opportunity import Opportunity, Application, ManualResolutionRequest
from core.status import OpportunityStatus, ApplicationStatus
from core.services.manual_resolution_service import resolve_request, resume_application

def test_resolve_request(db_session: Session):
    # Setup
    opp = Opportunity(
        company="Test",
        title="Test",
        url="https://unstop.com/competitions/123",
        dedup_hash="test-opp-1",
        status=OpportunityStatus.READY_TO_APPLY.value,
    )
    db_session.add(opp)
    db_session.commit()
    
    app = Application(
        opportunity_id=opp.id,
        status=ApplicationStatus.FAILED.value,
        attempt_number=1,
    )
    db_session.add(app)
    db_session.commit()
    
    req = ManualResolutionRequest(
        application_id=app.id,
        opportunity_id=opp.id,
        field_name="course_specialization",
        available_options=["Computer Science", "Mechanical Engineering"],
        reason="NO_EXACT_MATCH",
    )
    db_session.add(req)
    db_session.commit()
    
    # Test valid resolution
    resolved_req = resolve_request(db_session, req.id, "Computer Science", "test_user")
    assert resolved_req.status == "resolved"
    assert resolved_req.resolved_value == "Computer Science"
    assert resolved_req.resolved_by == "test_user"
    assert resolved_req.resolved_at is not None
    
    # Test invalid option
    req2 = ManualResolutionRequest(
        application_id=app.id,
        opportunity_id=opp.id,
        field_name="course_stream",
        available_options=["Science", "Arts"],
        reason="NO_EXACT_MATCH",
    )
    db_session.add(req2)
    db_session.commit()
    
    with pytest.raises(ValueError, match="is not in the available options"):
        resolve_request(db_session, req2.id, "Engineering", "test_user")

def test_resume_application(db_session: Session):
    opp = Opportunity(
        company="Test",
        title="Test",
        url="https://unstop.com/competitions/123",
        dedup_hash="test-opp-2",
        status=OpportunityStatus.MANUAL_APPLICATION_REQUIRED.value,
    )
    db_session.add(opp)
    db_session.commit()
    
    app = Application(
        opportunity_id=opp.id,
        status=ApplicationStatus.FAILED.value,
        attempt_number=1,
    )
    db_session.add(app)
    db_session.commit()
    
    req = ManualResolutionRequest(
        application_id=app.id,
        opportunity_id=opp.id,
        field_name="course_specialization",
        available_options=["CS"],
        reason="NO_EXACT_MATCH",
        status="resolved",
        resolved_value="CS",
    )
    db_session.add(req)
    db_session.commit()
    
    # Resume
    resumed_app = resume_application(db_session, app.id, "test_actor")
    
    # Assert transitions
    assert resumed_app.status == ApplicationStatus.PENDING.value
    assert opp.status == OpportunityStatus.READY_TO_APPLY.value
