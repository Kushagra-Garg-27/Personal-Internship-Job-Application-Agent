"""Seed script for populating the database with realistic Phase 6 dashboard demo data.

Populates:
1. Candidate Profile with versioned candidate resumes (active v2 and alternate v1).
2. Recommended opportunities with semantic relevance scores, matched skills, and reliability tiers.
3. Scam Review Pending opportunities with rule signals and Gemini LLM stated reasoning.

Usage:
    .venv\\Scripts\\python scripts/seed_dashboard.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone

from core.database import SessionLocal
from core.models.profile import Profile
from core.models.resume import Resume
from core.repositories import scoring_repo
from core.services import opportunity_service
from core.status import OpportunityStatus


def seed():
    session = SessionLocal()
    try:
        print("Checking existing candidate profiles...")
        profile = session.query(Profile).filter_by(email="alex.morgan@example.com").first()
        if not profile:
            profile = Profile(
                name="default",
                full_name="Alex Morgan",
                email="alex.morgan@example.com",
                phone="+1 (555) 349-2041",
                location="San Francisco, CA",
                salary_floor=140000,
                remote_preference="remote",
            )
            session.add(profile)
            session.flush()

            resume_v1 = Resume(
                profile_id=profile.id,
                version=1,
                original_filename="alex_morgan_swe_general.pdf",
                file_path="uploads/alex_morgan_v1.pdf",
                file_size_bytes=142300,
                is_active=False,
                parsed_text="Full-stack engineer experienced with React, Python, PostgreSQL, and Cloud APIs.",
            )
            resume_v2 = Resume(
                profile_id=profile.id,
                version=2,
                original_filename="alex_morgan_senior_ai_lead.pdf",
                file_path="uploads/alex_morgan_v2_active.pdf",
                file_size_bytes=184500,
                is_active=True,
                parsed_text=(
                    "Senior AI & Backend Systems Lead with deep expertise in Python, FastAPI, "
                    "PyTorch, Vector Embeddings, Distributed Pipelines, and modern React TypeScript frontends."
                ),
            )
            session.add_all([resume_v1, resume_v2])
            session.flush()
            print(f"Created profile {profile.name} (ID: {profile.id}) with 2 versioned resumes.")
        else:
            print(f"Using existing profile {profile.name} (ID: {profile.id}).")

        # ── Seed Recommended Opportunities ─────────────────────────────────
        recommended_jobs = [
            {
                "title": "Senior Machine Learning Engineer",
                "company": "Anthropic",
                "location": "San Francisco, CA (Hybrid / Remote)",
                "salary_min": 190000,
                "salary_max": 240000,
                "reliability_tier": "stable",
                "source": "greenhouse",
                "url": "https://boards.greenhouse.io/anthropic/jobs/senior-mle",
                "description": (
                    "Anthropic is looking for a Senior Machine Learning Engineer to scale our core training "
                    "and inference pipelines. You will design distributed systems, optimize transformer runtimes, "
                    "and collaborate closely with alignment researchers to deploy production foundation models."
                ),
                "score": 0.94,
                "explanation": {
                    "matched_skills": ["Python", "PyTorch", "Distributed Systems", "Transformer Architectures", "FastAPI"],
                    "top_skills": ["Python", "PyTorch", "LLMs"],
                    "match_summary": "Exceptional alignment with your deep learning pipeline background and distributed ML experience.",
                    "matched_sentences": [
                        {"sentence": "Design distributed systems and optimize transformer runtimes.", "similarity": 0.96},
                        {"sentence": "Collaborate closely with alignment researchers to deploy foundation models.", "similarity": 0.92},
                    ],
                },
            },
            {
                "title": "Staff Backend Systems Architect",
                "company": "Stripe",
                "location": "Remote, US",
                "salary_min": 210000,
                "salary_max": 260000,
                "reliability_tier": "stable",
                "source": "lever",
                "url": "https://jobs.lever.co/stripe/staff-backend-architect",
                "description": (
                    "Join Stripe's Core Infrastructure organization. We build high-throughput financial transaction engines "
                    "requiring sub-millisecond latencies, strict idempotency guarantees, and bulletproof database replication."
                ),
                "score": 0.89,
                "explanation": {
                    "matched_skills": ["Python", "PostgreSQL", "High-Throughput APIs", "Distributed Pipelines", "System Architecture"],
                    "top_skills": ["Python", "PostgreSQL", "Distributed Systems"],
                    "match_summary": "Strong match for backend architecture, data consistency, and scalable transaction processing.",
                    "matched_sentences": [
                        {"sentence": "Build high-throughput financial engines requiring sub-millisecond latencies.", "similarity": 0.91},
                        {"sentence": "Strict idempotency guarantees and bulletproof database replication.", "similarity": 0.87},
                    ],
                },
            },
            {
                "title": "Lead Full-Stack AI Engineer",
                "company": "Perplexity",
                "location": "Remote, US",
                "salary_min": 170000,
                "salary_max": 210000,
                "reliability_tier": "discovery_only",
                "source": "gmail_alert",
                "url": "https://perplexity.ai/careers/fullstack-ai-lead",
                "description": (
                    "We are seeking a Lead Full-Stack AI Engineer to spearhead new interactive conversational experiences. "
                    "You will bridge responsive React + TypeScript user interfaces with low-latency LLM streaming backends."
                ),
                "score": 0.84,
                "explanation": {
                    "matched_skills": ["React", "TypeScript", "FastAPI", "Vector Embeddings", "Python"],
                    "top_skills": ["React", "TypeScript", "Python"],
                    "match_summary": "Direct match for your full-stack React capabilities coupled with real-time AI backend endpoints.",
                    "matched_sentences": [
                        {"sentence": "Bridge responsive React user interfaces with low-latency LLM streaming backends.", "similarity": 0.93},
                    ],
                },
            },
            {
                "title": "Platform Engineer — ML Infrastructure",
                "company": "Cohere",
                "location": "Toronto, ON (Remote Option)",
                "salary_min": 155000,
                "salary_max": 190000,
                "reliability_tier": "stable",
                "source": "greenhouse",
                "url": "https://boards.greenhouse.io/cohere/jobs/platform-ml-infra",
                "description": (
                    "Build next-generation developer tooling and Kubernetes infrastructure for large language model deployment, "
                    "monitoring, and automated regression testing."
                ),
                "score": 0.78,
                "explanation": {
                    "matched_skills": ["Python", "Docker", "Kubernetes", "CI/CD", "Cloud APIs"],
                    "top_skills": ["Kubernetes", "Docker", "Python"],
                    "match_summary": "Solid overlap with your containerized backend deployments and operational automation skills.",
                    "matched_sentences": [
                        {"sentence": "Build next-generation tooling and Kubernetes infrastructure for model deployment.", "similarity": 0.81},
                    ],
                },
            },
        ]

        for job in recommended_jobs:
            existing = session.query(opportunity_service.Opportunity).filter_by(
                company=job["company"], title=job["title"]
            ).first()
            if existing:
                print(f"Skipping existing job: {job['title']} at {job['company']}")
                continue

            opp = opportunity_service.create_opportunity(
                session,
                title=job["title"],
                company=job["company"],
                url=job["url"],
                location=job["location"],
                salary_min=job["salary_min"],
                salary_max=job["salary_max"],
                reliability_tier=job["reliability_tier"],
                source=job["source"],
                description=job["description"],
                profile_id=profile.id,
            )
            opportunity_service.transition_status(
                session,
                opportunity_id=opp.id,
                new_status=OpportunityStatus.RECOMMENDED,
                reason="Funnel Stage 1-3 completed: High semantic relevance",
                actor="funnel_runner",
            )
            scoring_repo.upsert_verdict(
                session,
                opportunity_id=opp.id,
                profile_id=profile.id,
                eligibility_passed=True,
                eligibility_reason={"detail": "Passed all compensation and remote preferences"},
                scam_verdict="clear",
                scam_reason={"detail": "Passed duplicate signatures and heuristic rules"},
                relevance_score=job["score"],
                relevance_explanation=job["explanation"],
                model_name="all-MiniLM-L6-v2",
                funnel_completed_at=datetime.now(timezone.utc),
            )
            print(f"Added recommended opportunity: {opp.title} ({opp.company}) — Score: {job['score']}")

        # ── Seed Scam Review Pending Opportunities ─────────────────────────
        scam_jobs = [
            {
                "title": "Executive Assistant to Managing Partner",
                "company": "Global Wealth Ventures LLC",
                "location": "Fully Remote",
                "salary_min": 120000,
                "salary_max": 140000,
                "reliability_tier": "discovery_only",
                "source": "gmail_alert",
                "url": "https://globalwealthventures-careers.info/jobs/ea",
                "description": (
                    "Immediate opening for Executive Assistant. Candidates must conduct interview via Telegram messenger. "
                    "A check of $4,500 will be issued upon hire for equipment purchase from our designated vendor."
                ),
                "scam_verdict": "ambiguous",
                "scam_reason": {
                    "rule": "keyword_blocklist",
                    "signals": ["buy equipment from our vendor", "telegram interview"],
                },
                "llm_verdict": "suspicious",
                "llm_reasoning": (
                    "Listing requests interview conduct strictly through third-party messenger (Telegram) and specifies "
                    "an equipment advance check scheme, consistent with known fake check / advance-fee employment scams."
                ),
            },
            {
                "title": "Remote Senior Cloud Operations Associate",
                "company": "Vertex Technologies Inc",
                "location": "Remote",
                "salary_min": 130000,
                "salary_max": 150000,
                "reliability_tier": "discovery_only",
                "source": "rss_feed",
                "url": "http://vertex-tech-jobs2026.biz/cloud-associate",
                "description": (
                    "Vertex Technologies is hiring Cloud Associates. Send resume directly to recruiting.vertextech@gmail.com "
                    "for immediate expedited consideration."
                ),
                "scam_verdict": "ambiguous",
                "scam_reason": {
                    "rule": "free_email_recruiter",
                    "signals": ["recruiting.vertextech@gmail.com on corporate entity"],
                },
                "llm_verdict": "suspicious",
                "llm_reasoning": (
                    "Established tech corporate brand name recruiting exclusively through an unregistered consumer "
                    "@gmail.com domain with domain age less than 14 days old."
                ),
            },
        ]

        for job in scam_jobs:
            existing = session.query(opportunity_service.Opportunity).filter_by(
                company=job["company"], title=job["title"]
            ).first()
            if existing:
                print(f"Skipping existing scam job: {job['title']} at {job['company']}")
                continue

            opp = opportunity_service.create_opportunity(
                session,
                title=job["title"],
                company=job["company"],
                url=job["url"],
                location=job["location"],
                salary_min=job["salary_min"],
                salary_max=job["salary_max"],
                reliability_tier=job["reliability_tier"],
                source=job["source"],
                description=job["description"],
                profile_id=profile.id,
            )
            opportunity_service.transition_status(
                session,
                opportunity_id=opp.id,
                new_status=OpportunityStatus.SCAM_REVIEW_PENDING,
                reason="Stage 2 Funnel: Ambiguous scam signal detected, awaiting human review",
                actor="funnel_runner",
            )
            scoring_repo.upsert_verdict(
                session,
                opportunity_id=opp.id,
                profile_id=profile.id,
                eligibility_passed=True,
                scam_verdict=job["scam_verdict"],
                scam_reason=job["scam_reason"],
                llm_verdict=job["llm_verdict"],
                llm_reasoning=job["llm_reasoning"],
                relevance_score=None,
                relevance_explanation=None,
            )
            print(f"Added scam review pending item: {opp.title} ({opp.company})")

        session.commit()
        print("\nSeed completed successfully!")

    finally:
        session.close()


if __name__ == "__main__":
    seed()
