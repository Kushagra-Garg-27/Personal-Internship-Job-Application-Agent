"""Unit tests for Phase 4 semantic relevance scorer and RelevanceStage."""

from __future__ import annotations

import pytest

from core.funnel.relevance import RelevanceScorer, RelevanceStage, split_sentences


class DummySkill:
    def __init__(self, skill_name: str):
        self.skill_name = skill_name


class DummyResume:
    def __init__(self, parsed_text: str, is_active: bool = True):
        self.parsed_text = parsed_text
        self.is_active = is_active


class DummyProfile:
    def __init__(self, skills=None, resumes=None):
        self.skills = skills or []
        self.resumes = resumes or []


class DummyOpp:
    def __init__(self, description: str | None):
        self.description = description


@pytest.fixture(scope="module")
def shared_scorer():
    return RelevanceScorer()


class TestSentenceSplitter:
    def test_split_sentences(self):
        text = "FastAPI is modern. We build microservices! Are you ready for Python? Yes we are."
        sentences = split_sentences(text)
        assert len(sentences) >= 2
        assert any("microservices" in s for s in sentences)

    def test_split_empty(self):
        assert split_sentences("") == []


class TestRelevanceScorer:
    def test_relative_ordering(self, shared_scorer):
        """Python developer profile should score significantly higher on Python job than Chef job."""
        resume = (
            "Senior Backend Engineer with 5 years building scalable APIs using Python, "
            "FastAPI, PostgreSQL, and Docker. Strong experience in async architectures."
        )
        skills = ["Python", "FastAPI", "PostgreSQL", "Docker", "REST API"]

        python_job = (
            "We are seeking a Senior Python Engineer to lead backend microservice development. "
            "Required skills: Python, FastAPI, relational databases (PostgreSQL preferred), "
            "and containerized deployment with Docker."
        )
        chef_job = (
            "Head Chef required for luxury Italian restaurant. Must be proficient in classic pasta "
            "making, kitchen staff supervision, food sanitation safety, and seasonal menu planning."
        )

        res_python = shared_scorer.score(resume_text=resume, skills=skills, description=python_job)
        res_chef = shared_scorer.score(resume_text=resume, skills=skills, description=chef_job)

        assert res_python["score"] > res_chef["score"]
        assert res_python["score"] > 0.6
        assert res_chef["score"] < 0.35

    def test_explanation_structure(self, shared_scorer):
        """Ensure explanation dictionary contains top_skills and top_sentences."""
        resume = "Data Analyst skilled in SQL, Pandas, and Tableau."
        skills = ["SQL", "Pandas", "Tableau"]
        job = "Looking for a Data Analyst to build SQL queries and Tableau dashboards."

        result = shared_scorer.score(resume_text=resume, skills=skills, description=job)
        assert "score" in result
        assert "explanation" in result
        explanation = result["explanation"]
        assert "top_skills" in explanation
        assert "top_sentences" in explanation

        top_skills = explanation["top_skills"]
        assert len(top_skills) <= 5
        for item in top_skills:
            assert "skill" in item
            assert "similarity" in item

    def test_explanation_accuracy(self, shared_scorer):
        """Top matched skill should be the one most relevant to the job."""
        skills = ["Kubernetes", "Pastry Baking", "Graphic Design"]
        job = "DevOps Engineer needed to manage production Kubernetes clusters and helm charts."

        result = shared_scorer.score(resume_text="", skills=skills, description=job)
        top_skill = result["explanation"]["top_skills"][0]
        assert top_skill["skill"] == "Kubernetes"

    def test_determinism(self, shared_scorer):
        """Identical inputs must yield identical scores and explanations."""
        resume = "Machine learning engineer experienced in PyTorch, NLP, and LLM fine-tuning."
        skills = ["PyTorch", "NLP", "Transformers"]
        job = "Seeking NLP Specialist to develop generative AI workflows and fine-tune transformer models."

        res1 = shared_scorer.score(resume_text=resume, skills=skills, description=job)
        res2 = shared_scorer.score(resume_text=resume, skills=skills, description=job)

        assert res1["score"] == res2["score"]
        assert res1["explanation"] == res2["explanation"]

    def test_missing_description(self, shared_scorer):
        result = shared_scorer.score(resume_text="Some resume", skills=["Python"], description=None)
        assert result["score"] == 0.0
        assert "Empty job description" in result["explanation"]["note"]

    def test_missing_candidate_data(self, shared_scorer):
        result = shared_scorer.score(resume_text="", skills=[], description="Some job")
        assert result["score"] == 0.0
        assert "No candidate resume" in result["explanation"]["note"]


class TestRelevanceStage:
    def test_evaluate_with_profile_and_resume(self):
        stage = RelevanceStage()
        opp = DummyOpp(description="Python web developer with Django or FastAPI experience.")
        profile = DummyProfile(
            skills=[DummySkill("Python"), DummySkill("FastAPI")],
            resumes=[DummyResume("Backend Python developer.", is_active=True)],
        )

        verdict = stage.evaluate(opportunity=opp, profile=profile)
        assert verdict.stage_name == "relevance"
        assert verdict.passed is True
        assert verdict.payload is not None
        assert "score" in verdict.payload
        assert verdict.payload["score"] > 0.4
        assert len(verdict.payload["explanation"]["top_skills"]) >= 1
