"""LaTeX resume generation engine (`latex_tailor`)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from jobscout import latex_tailor as lt
from jobscout.models import Job, UserProfile
from jobscout.tailor import TailoringError

_PLAN = {
    "name": "Jane Q Public",
    "contact_line": "jane@example.com · Boston, MA · US Citizen",
    "summary": "Data engineer who cut pipeline latency and shipped ML features.",
    "experience": [{
        "company": "Acme Data", "location": "Boston, MA", "title": "Senior Data Engineer",
        "dates": "2021–Present",
        "bullets": ["Cut nightly ETL runtime from 6 hours to 40 minutes.",
                    "Built a feature store used by 3 teams."],
    }],
    "education": [{"degree": "MS Computer Science", "institution": "MIT", "year": "2018"}],
    "skills": [{"category": "Languages", "items": "Python, SQL, Rust"}],
    "additional": [{"title": "Publications", "lines": ["A paper on streaming joins."]}],
}
_SOURCE = (
    "Jane Q Public. jane@example.com. Boston, MA. US Citizen. "
    "Senior Data Engineer at Acme Data (2021-Present): cut nightly ETL runtime from 6 hours "
    "to 40 minutes; built a feature store used by 3 teams. "
    "MS Computer Science, MIT, 2018. Python, SQL, Rust. Publication on streaming joins."
)


class TestEscape:
    def test_escapes_special_chars(self) -> None:
        assert lt._esc("A & B 50% $5 #1 a_b") == r"A \& B 50\% \$5 \#1 a\_b"

    def test_em_dash_normalized(self) -> None:
        assert "—" not in lt._esc("2021—2024")


class TestValidatePlan:
    def test_valid_plan_passes(self) -> None:
        plan = lt._validate_plan(_PLAN)
        assert plan["name"] == "Jane Q Public"
        assert len(plan["experience"]) == 1

    def test_missing_name_raises(self) -> None:
        with pytest.raises(TailoringError):
            lt._validate_plan({**_PLAN, "name": ""})

    def test_no_experience_raises(self) -> None:
        with pytest.raises(TailoringError):
            lt._validate_plan({**_PLAN, "experience": []})

    def test_non_dict_raises(self) -> None:
        with pytest.raises(TailoringError):
            lt._validate_plan(["not", "a", "dict"])

    def test_bullets_capped(self) -> None:
        big = {**_PLAN, "experience": [{**_PLAN["experience"][0],
                                        "bullets": [f"Bullet {i}." for i in range(50)]}]}
        plan = lt._validate_plan(big)
        assert len(plan["experience"][0]["bullets"]) == lt._MAX_BULLETS


class TestAudit:
    def test_grounded_plan_has_no_warnings(self) -> None:
        assert lt.audit_plan(lt._validate_plan(_PLAN), _SOURCE) == []

    def test_ungrounded_employer_flagged(self) -> None:
        plan = lt._validate_plan({**_PLAN, "experience": [
            {**_PLAN["experience"][0], "company": "Umbrella Corporation Fabricated"}]})
        warnings = lt.audit_plan(plan, _SOURCE)
        assert any("employer" in w for w in warnings)

    def test_audit_never_raises(self) -> None:
        # Even nonsense input returns a list rather than raising.
        assert isinstance(lt.audit_plan(lt._validate_plan(_PLAN), ""), list)


class TestRenderers:
    def test_latex_fills_placeholders(self) -> None:
        template = lt._template_path().read_text(encoding="utf-8")
        tex = lt._plan_to_latex(lt._validate_plan(_PLAN), template)
        assert "%%NAME%%" not in tex and "%%EXPERIENCE%%" not in tex
        assert "Jane Q Public" in tex
        assert r"\roleheader{Acme Data}" in tex
        assert r"\skillcat{Languages}" in tex

    def test_markdown_has_sections(self) -> None:
        md = lt._plan_to_markdown(lt._validate_plan(_PLAN))
        assert "# Jane Q Public" in md
        assert "## Professional Experience" in md
        assert "- Cut nightly ETL runtime" in md

    def test_plaintext_for_metrics(self) -> None:
        txt = lt._plan_plaintext(lt._validate_plan(_PLAN))
        assert "feature store" in txt


def _job() -> Job:
    return Job(job_id="j1", source="greenhouse", title="Data Engineer",
               company="Acme Data", url="http://x/j1")


def _profile() -> UserProfile:
    return UserProfile(label="jane", resume_text=_SOURCE)


class TestBuildWiring:
    """End-to-end wiring with compilation stubbed (no xelatex/pandoc needed)."""

    def test_build_returns_metrics_and_engine(self, monkeypatch, tmp_path) -> None:  # noqa: ANN001
        monkeypatch.setattr(lt, "llm_is_configured", lambda: True)
        monkeypatch.setattr(lt, "chat_json", lambda *_a, **_k: json.dumps(_PLAN))
        monkeypatch.setattr(lt, "active_llm_configuration",
                            lambda: ("deepseek", "key", "deepseek-chat"))
        monkeypatch.setattr(lt, "tailored_resume_path",
                            lambda pid, jid: tmp_path / pid / f"{jid}.docx")

        def _fake_pdf(_tex, workdir):  # noqa: ANN001
            p = Path(workdir) / "resume.pdf"
            p.write_bytes(b"%PDF-1.4 fake")
            return p

        def _fake_docx(_md, workdir):  # noqa: ANN001
            p = Path(workdir) / "resume.docx"
            p.write_bytes(b"PK fake docx")
            return p

        monkeypatch.setattr(lt, "_compile_pdf", _fake_pdf)
        monkeypatch.setattr(lt, "_compile_docx", _fake_docx)

        result = lt.build_latex_resume(_job(), _profile())
        assert result.engine == "latex"
        assert result.path.is_file() and result.pdf_path.is_file()
        assert result.filename.endswith(".docx")
        assert result.metrics["ai_risk_after"] is not None
        assert "delta" in result.metrics

    def test_no_resume_text_raises(self, monkeypatch) -> None:  # noqa: ANN001
        monkeypatch.setattr(lt, "llm_is_configured", lambda: True)
        with pytest.raises(TailoringError):
            lt.build_latex_resume(_job(), UserProfile(label="empty", resume_text=""))


class TestDashboardRoutes:
    """The per-candidate dashboard + per-job metrics endpoints."""

    def test_dashboard_shape_empty(self, client) -> None:  # noqa: ANN001
        pid = client.post("/api/profiles", json={"label": "cand"}).json()["id"]
        body = client.get(f"/api/profiles/{pid}/dashboard").json()
        assert body["profile"]["id"] == pid
        assert body["tailored"] == []
        assert body["pipeline"]["total_applications"] == 0

    def test_dashboard_includes_pipeline_funnel(self, client) -> None:  # noqa: ANN001
        pid = client.post("/api/profiles", json={"label": "cand"}).json()["id"]
        client.post(f"/api/profiles/{pid}/job-state",
                    json={"job_id": "j1", "status": "interview"})
        body = client.get(f"/api/profiles/{pid}/dashboard").json()
        assert body["pipeline"]["total_applications"] == 1
        assert body["pipeline"]["by_stage"]["interview"] == 1

    def test_metrics_404_when_none(self, client) -> None:  # noqa: ANN001
        pid = client.post("/api/profiles", json={"label": "cand"}).json()["id"]
        assert client.get(f"/api/profiles/{pid}/tailored/j1/metrics").status_code == 404

    def test_pdf_404_when_none(self, client) -> None:  # noqa: ANN001
        pid = client.post("/api/profiles", json={"label": "cand"}).json()["id"]
        assert client.get(f"/api/profiles/{pid}/tailored/j1/pdf").status_code == 404


@pytest.mark.skipif(
    shutil.which("xelatex") is None or shutil.which("pandoc") is None,
    reason="xelatex + pandoc required for a real LaTeX build",
)
class TestRealBuild:
    def test_real_pdf_and_docx(self, monkeypatch, tmp_path) -> None:  # noqa: ANN001
        monkeypatch.setattr(lt, "llm_is_configured", lambda: True)
        monkeypatch.setattr(lt, "chat_json", lambda *_a, **_k: json.dumps(_PLAN))
        monkeypatch.setattr(lt, "active_llm_configuration",
                            lambda: ("deepseek", "key", "deepseek-chat"))
        monkeypatch.setattr(lt, "tailored_resume_path",
                            lambda pid, jid: tmp_path / pid / f"{jid}.docx")
        result = lt.build_latex_resume(_job(), _profile())
        assert result.pdf_path.is_file() and result.pdf_path.stat().st_size > 1000
        assert result.path.is_file()
