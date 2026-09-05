"""Seed the database with sample recruiter messages for frontend development.

Usage:
    cd job-app-agent
    .venv\\Scripts\\python scripts/seed_messages.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.database import SessionLocal
from core.models.base import Base
from core.database import engine
from core.models import (  # noqa: F401 — force model registration
    RecruiterMessage,
    IntegrationHealthEvent,
    Opportunity,
    Application,
)
from core.status import OpportunityStatus


def seed():
    Base.metadata.create_all(engine)
    db = SessionLocal()

    try:
        # Check if messages already exist
        existing = db.query(RecruiterMessage).count()
        if existing > 0:
            print(f"⚠ {existing} messages already exist. Skipping seed.")
            return

        # ── Create sample opportunities + applications ────────────────
        now = datetime.now(timezone.utc)
        opps_data = [
            ("Backend Engineer", "Google", "https://google.com/careers/123"),
            ("Frontend Developer", "Stripe", "https://stripe.com/jobs/456"),
            ("ML Researcher", "DeepMind", "https://deepmind.com/careers/789"),
            ("SRE Intern", "Netflix", "https://netflix.com/jobs/012"),
        ]

        apps = []
        for title, company, url in opps_data:
            opp = Opportunity(
                dedup_hash=f"seed_msg_{company.lower().replace(' ', '_')}_{title[:10]}",
                title=title,
                company=company,
                url=url,
                status=OpportunityStatus.SUBMITTED.value,
                source="greenhouse",
            )
            db.add(opp)
            db.flush()
            app = Application(
                opportunity_id=opp.id,
                status="submitted",
            )
            db.add(app)
            db.flush()
            apps.append((app, opp))

        print(f"✓ Created {len(apps)} sample applications")

        # ── Create sample messages ────────────────────────────────────
        messages = [
            RecruiterMessage(
                gmail_id="seed_msg_001",
                thread_id="seed_thread_1",
                application_id=apps[0][0].id,
                sender="Sarah Chen <sarah.chen@google.com>",
                sender_domain="google.com",
                subject="Interview Invitation — Backend Engineer",
                body_preview=(
                    "Hi! Thank you for your application to the Backend Engineer role. "
                    "We'd like to schedule a technical phone screen. "
                    "Please let us know your availability next week."
                ),
                received_at=now - timedelta(hours=2),
                classification="interview_invite",
                classification_source="rules",
                classification_confidence=0.92,
                link_confidence="high",
                raw_headers_json={
                    "from": "sarah.chen@google.com",
                    "date": (now - timedelta(hours=2)).isoformat(),
                },
            ),
            RecruiterMessage(
                gmail_id="seed_msg_002",
                thread_id="seed_thread_2",
                application_id=apps[1][0].id,
                sender="Stripe Recruiting <recruiting@stripe.com>",
                sender_domain="stripe.com",
                subject="Re: Your application for Frontend Developer",
                body_preview=(
                    "Unfortunately, after careful review we have decided not to "
                    "move forward with your application at this time. We appreciate "
                    "your interest in Stripe and encourage you to apply again."
                ),
                received_at=now - timedelta(hours=5),
                classification="rejection",
                classification_source="rules",
                classification_confidence=0.90,
                link_confidence="high",
                raw_headers_json={
                    "from": "recruiting@stripe.com",
                    "date": (now - timedelta(hours=5)).isoformat(),
                },
            ),
            RecruiterMessage(
                gmail_id="seed_msg_003",
                thread_id="seed_thread_3",
                application_id=apps[2][0].id,
                sender="DeepMind HR <hr@deepmind.com>",
                sender_domain="deepmind.com",
                subject="Coding Challenge — ML Researcher Position",
                body_preview=(
                    "As the next step in your application process, we'd like you "
                    "to complete a take-home coding challenge. You'll have 72 hours "
                    "to complete it. The link will be sent separately."
                ),
                received_at=now - timedelta(hours=8),
                classification="screening_question",
                classification_source="rules",
                classification_confidence=0.88,
                link_confidence="high",
                raw_headers_json={
                    "from": "hr@deepmind.com",
                    "date": (now - timedelta(hours=8)).isoformat(),
                },
            ),
            RecruiterMessage(
                gmail_id="seed_msg_004",
                thread_id="seed_thread_4",
                sender="unknown-recruiter@newstartup.io",
                sender_domain="newstartup.io",
                subject="Exciting Opportunity",
                body_preview=(
                    "I came across your profile and wanted to reach out about "
                    "an exciting opportunity at our company. Would you be "
                    "interested in a conversation?"
                ),
                received_at=now - timedelta(hours=12),
                classification="follow_up",
                classification_source="llm",
                classification_confidence=0.75,
                link_confidence="none",
            ),
            RecruiterMessage(
                gmail_id="seed_msg_005",
                sender="noreply@greenhouse.io",
                sender_domain="greenhouse.io",
                subject="Application Confirmation",
                body_preview=(
                    "Thank you for applying! We have received your application "
                    "and will review it shortly. You will hear from us within 2 weeks."
                ),
                received_at=now - timedelta(days=1),
                classification="generic",
                classification_source="rules",
                classification_confidence=0.90,
                link_confidence="none",
            ),
            RecruiterMessage(
                gmail_id="seed_msg_006",
                thread_id="seed_thread_1",
                application_id=apps[0][0].id,
                sender="Sarah Chen <sarah.chen@google.com>",
                sender_domain="google.com",
                subject="Re: Interview Invitation — Backend Engineer",
                body_preview=(
                    "Great! I've confirmed your interview slot for Thursday at 2 PM PST. "
                    "You'll receive a calendar invite shortly. The interview will be "
                    "a 45-minute technical discussion with two engineers."
                ),
                received_at=now - timedelta(hours=1),
                classification="interview_invite",
                classification_source="rules",
                classification_confidence=0.91,
                link_confidence="high",
                raw_headers_json={
                    "from": "sarah.chen@google.com",
                    "date": (now - timedelta(hours=1)).isoformat(),
                },
            ),
            RecruiterMessage(
                gmail_id="seed_msg_007",
                application_id=apps[3][0].id,
                sender="Netflix Talent <talent@netflix.com>",
                sender_domain="netflix.com",
                subject="Congratulations — Offer for SRE Intern",
                body_preview=(
                    "We are pleased to offer you the SRE Intern position at Netflix! "
                    "Your offer letter is attached. Please review the compensation "
                    "package and let us know if you have any questions."
                ),
                received_at=now - timedelta(minutes=30),
                classification="offer",
                classification_source="rules",
                classification_confidence=0.95,
                link_confidence="high",
            ),
            RecruiterMessage(
                gmail_id="seed_msg_008",
                sender="recruiter@ambiguous.com",
                sender_domain="ambiguous.com",
                subject="Quick question about your availability",
                body_preview=(
                    "Hi, I wanted to check if you're still available for a chat "
                    "about a role we discussed previously."
                ),
                received_at=now - timedelta(days=2),
                classification="unclassified",
                classification_source="rules",
                classification_confidence=0.0,
                link_confidence="low",
            ),
        ]

        db.add_all(messages)
        print(f"✓ Created {len(messages)} sample messages")

        # ── Create health events ──────────────────────────────────────
        health_events = [
            IntegrationHealthEvent(
                integration_name="gmail_response_poller",
                event_type="poll_success",
                detail=f"Processed 3 messages (historyId: 12345)",
            ),
            IntegrationHealthEvent(
                integration_name="gmail_response_poller",
                event_type="poll_success",
                detail=f"No new messages (historyId: 12346)",
            ),
        ]
        db.add_all(health_events)
        print(f"✓ Created {len(health_events)} health events")

        db.commit()
        print("\n✅ Seed complete! Response Center is ready for testing.")

    except Exception as e:
        db.rollback()
        print(f"\n✗ Seed failed: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed()
