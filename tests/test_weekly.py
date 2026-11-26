import json
from datetime import UTC, date, datetime, timedelta

from brief.config import ROOT, load_config
from brief.feedback import (
    add_to_library,
    apply_callback_updates,
    build_piece_keyboard,
    effective_topic_weights,
    load_feedback,
    register_pieces_in_catalog,
)
from brief.sources.tech_sources import TechCandidate, parse_arxiv_xml, parse_hf_daily_papers, parse_hn_item
from brief.weekly import (
    ROLE_ACCESSIBLE,
    ROLE_APPLIED,
    ROLE_DEEP,
    ROLE_GEM,
    ROLE_NEWS,
    ROLE_TOOL,
    build_weekly_digest,
    choose_weekly_format,
    dedupe_candidates,
    format_weekly_messages,
    parse_week_id,
    write_digest_archive,
)

NOW = datetime(2026, 11, 29, 18, 0, tzinfo=UTC)


def _sample_candidates() -> list[TechCandidate]:
    return [
        TechCandidate(
            title="Quantization of Small Language Models for Edge Classroom Tutoring",
            url="https://arxiv.org/abs/2611.00001",
            source="arXiv",
            kind="paper",
            published=NOW - timedelta(days=1),
            abstract=(
                "We evaluate 4-bit quantization and distillation on a 3B small language model "
                "for offline student tutoring and assessment."
            ),
            authors=["A. Gómez", "B. Pérez"],
            categories=["cs.CL", "cs.CY"],
            default_topic="edtech",
            votes=42,
        ),
        TechCandidate(
            title="Convergence Bounds for Stochastic Variational Bayesian Optimization",
            url="https://arxiv.org/abs/2611.00002",
            source="arXiv",
            kind="paper",
            published=NOW - timedelta(days=2),
            abstract=(
                "We prove tight theoretical convergence bounds for stochastic gradient "
                "Markov chain Monte Carlo under non-convex objectives."
            ),
            authors=["C. Ruiz"],
            categories=["math.OC", "stat.ML"],
            default_topic="prob_optimization",
            citations=12,
        ),
        TechCandidate(
            title="Tabular Extraction Benchmark for Complex Structured Documents in Production",
            url="https://arxiv.org/abs/2611.00003",
            source="Hugging Face Papers",
            kind="paper",
            published=NOW - timedelta(days=1),
            abstract=(
                "An empirical benchmark and fine-tuning pipeline for extracting tables "
                "and schemas from noisy PDF documents with low latency."
            ),
            authors=["D. Silva"],
            categories=["cs.LG", "cs.AI"],
            default_topic="ml_applied",
            votes=88,
        ),
        TechCandidate(
            title="New Open-Weight 3B Multimodal Model Runs Offline on Laptops",
            url="https://www.technologyreview.com/2026/11/28/slm-offline",
            source="MIT Technology Review",
            kind="news",
            published=NOW - timedelta(days=1),
            abstract=(
                "Researchers released a compact multimodal model with GGUF quantization "
                "for local inference on standard hardware."
            ),
            default_topic="small_models_edge",
            votes=120,
        ),
        TechCandidate(
            title="Colombia lanza nuevo portal de datos abiertos con API para transporte y educación",
            url="https://example.org/datos-abiertos-colombia-2026",
            source="Reporte LatAm",
            kind="news",
            published=NOW - timedelta(days=2),
            abstract=(
                "El portal integra datasets abiertos de movilidad, educación "
                "y contratación en Bogotá y Latinoamérica."
            ),
            default_topic="open_data_latam",
            language="es",
            votes=65,
        ),
        TechCandidate(
            title="Show HN: LiteTable – Fast Rust and Python CLI for SQLite and Tabular Pipelines",
            url="https://github.com/example/litetable",
            source="Hacker News",
            kind="tool",
            published=NOW - timedelta(days=1),
            abstract=(
                "An open source library and CLI tool in Rust with Python bindings "
                "to validate, query and version control tabular datasets."
            ),
            default_topic="software_engineering",
            votes=210,
        ),
        TechCandidate(
            title="Civic Corpus: A Low-Resource Multilingual Dataset for Latin American Public Policy",
            url="https://arxiv.org/abs/2611.00009",
            source="Semantic Scholar",
            kind="paper",
            published=NOW - timedelta(days=3),
            abstract=(
                "A curated open data corpus in Spanish for evaluating retrieval "
                "and classification on Latin American civic documents."
            ),
            authors=["M. Pardo"],
            default_topic="open_data_latam",
            votes=8,
            citations=4,
        ),
    ]


def test_curation_selects_7_diverse_pieces_with_project_connections(tmp_path):
    cfg = load_config()
    fb_path = tmp_path / "feedback.json"
    digest = build_weekly_digest(
        cfg,
        candidates=_sample_candidates(),
        ref_date=date(2026, 11, 29),
        feedback_path=fb_path,
    )
    assert digest.week_id == "2026-W48"
    assert len(digest.pieces) == 7

    roles = [p.role for p in digest.pieces]
    assert ROLE_ACCESSIBLE in roles
    assert ROLE_DEEP in roles
    assert ROLE_APPLIED in roles
    assert roles.count(ROLE_NEWS) == 2
    assert ROLE_TOOL in roles
    assert ROLE_GEM in roles

    topics = {p.topic for p in digest.pieces}
    assert len(topics) >= 4

    for p in digest.pieces:
        assert p.title_es and p.hook and p.url.startswith("http")
        assert 1 <= len(p.summary_es.split()) <= 40
        assert p.reading_minutes >= 3
        if p.kind == "paper":
            assert any(proj in p.project_line for proj in ("Aula Ya", "Table Master", "PostMark"))


def test_deduplication_and_short_digest_when_few_candidates(tmp_path):
    c1 = _sample_candidates()[0]
    c2 = TechCandidate(
        title="Quantization of Small Language Models for Edge Classroom Tutoring!",
        url="https://huggingface.co/papers/2611.00001",
        source="Hugging Face Papers",
        kind="paper",
        published=NOW,
        abstract=c1.abstract,
        score=10.0,
    )
    c1.score = 12.0
    deduped = dedupe_candidates([c1, c2], threshold=78.0)
    assert len(deduped) == 1 and deduped[0].url == c1.url

    cfg = load_config()
    short_digest = build_weekly_digest(
        cfg,
        candidates=[c1],
        ref_date=date(2026, 11, 29),
        feedback_path=tmp_path / "fb.json",
    )
    assert len(short_digest.pieces) == 1
    msgs, _ = format_weekly_messages(short_digest)
    assert all(len(m) <= 4096 for m in msgs)


def test_format_rotation_and_message_splitting(tmp_path):
    idx1, fmt1 = choose_weekly_format(48, last_format_index=None)
    idx2, fmt2 = choose_weekly_format(48, last_format_index=idx1)
    assert fmt1 != fmt2 and idx1 != idx2

    cfg = load_config()
    digest = build_weekly_digest(
        cfg,
        candidates=_sample_candidates(),
        week_override="2026-W48",
        feedback_path=tmp_path / "fb.json",
    )
    msgs, markups = format_weekly_messages(digest)
    assert 2 <= len(msgs) <= 3
    assert all(len(m) <= 4096 for m in msgs)
    assert len(markups) == len(msgs)
    assert markups[0] is not None and "inline_keyboard" in markups[0]
    assert "Concepto en 60 segundos" in msgs[-1]
    assert "Reto de la semana" in msgs[-1]
    assert digest.quiz.options and 0 <= digest.quiz.correct_option_id < len(digest.quiz.options)


def test_callback_processing_no_duplicates_and_weight_updates_and_library(tmp_path):
    fb_path = tmp_path / "feedback.json"
    lib_json = tmp_path / "library.json"
    lib_dir = tmp_path / "biblioteca"

    register_pieces_in_catalog(
        [
            {
                "id": "p_1234",
                "title_es": "Cuantización de modelos pequeños en el aula",
                "url": "https://arxiv.org/abs/2611.00001",
                "topic": "small_models_edge",
                "topic_label": "Modelos pequeños y edge/offline AI",
                "role": "Paper aplicado",
                "hook": "Corre en laptops sin GPU",
                "summary_es": "Resumen corto de prueba.",
                "week": "2026-W48",
                "date": "2026-11-29",
            }
        ],
        path=fb_path,
    )

    updates = [
        {"update_id": 100, "callback_query": {"id": "cb1", "data": "save:p_1234"}},
        {"update_id": 101, "callback_query": {"id": "cb2", "data": "more:p_1234:small_models_edge"}},
        {"update_id": 102, "callback_query": {"id": "cb3", "data": "less:p_1234:software_engineering"}},
        {"update_id": 103, "callback_query": {"id": "cb4", "data": "read:p_1234"}},
    ]
    answered: list[tuple[str, str]] = []
    res1 = apply_callback_updates(
        updates,
        feedback_path=fb_path,
        library_json=lib_json,
        library_dir=lib_dir,
        answer_fn=lambda cid, txt: answered.append((cid, txt)),
    )
    assert (res1["processed"], res1["saved"], res1["more"], res1["less"], res1["read"]) == (4, 1, 1, 1, 1)
    assert len(answered) == 4

    res2 = apply_callback_updates(updates, feedback_path=fb_path, library_json=lib_json, library_dir=lib_dir)
    assert res2["processed"] == 0 and res2["skipped_duplicate"] == 4

    state = load_feedback(fb_path)
    assert state["last_update_id"] == 103
    interests = load_config().section("weekly_digest")["interests"]
    weights = effective_topic_weights(interests, state)
    assert weights["small_models_edge"] > interests["small_models_edge"]["weight"]
    assert weights["software_engineering"] < interests["software_engineering"]["weight"]

    saved_items = json.loads(lib_json.read_text(encoding="utf-8"))
    assert len(saved_items) == 1 and saved_items[0]["id"] == "p_1234"
    md_text = (lib_dir / "2026.md").read_text(encoding="utf-8")
    assert "## Modelos pequeños y edge/offline AI" in md_text
    assert "[Cuantización de modelos pequeños en el aula](https://arxiv.org/abs/2611.00001)" in md_text
    assert not add_to_library(saved_items[0], library_json=lib_json, library_dir=lib_dir)


def test_archive_writes_markdown_readme_and_docs_json(tmp_path):
    cfg = load_config()
    digest = build_weekly_digest(
        cfg,
        candidates=_sample_candidates(),
        week_override="2026-W48",
        feedback_path=tmp_path / "fb.json",
    )
    digests_dir = tmp_path / "digests"
    docs_dir = tmp_path / "docs"
    md_path = write_digest_archive(digest, digests_dir=digests_dir, docs_dir=docs_dir)
    assert md_path == digests_dir / "2026" / "2026-W48.md"
    assert md_path.exists()
    assert "2026-W48" in (digests_dir / "README.md").read_text(encoding="utf-8")
    data = json.loads((docs_dir / "digests.json").read_text(encoding="utf-8"))
    assert data[0]["week_id"] == "2026-W48" and len(data[0]["pieces"]) == 7


def test_parsers_and_workflows_and_week_id_validation():
    y, w, sun = parse_week_id("2026-W48")
    assert (y, w, sun.weekday()) == (2026, 48, 6)
    kb = build_piece_keyboard("p_1", "edtech")
    assert len(kb["inline_keyboard"]) == 2

    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>http://arxiv.org/abs/2611.12345v1</id>
        <published>2026-11-25T10:00:00Z</published>
        <title>Small Language Model Optimization</title>
        <summary>Fast quantization method.</summary>
        <author><name>J. Doe</name></author>
        <category term="cs.LG"/>
      </entry>
    </feed>"""
    parsed_arxiv = parse_arxiv_xml(xml)
    assert len(parsed_arxiv) == 1 and parsed_arxiv[0].authors == ["J. Doe"]

    hf_row = {"paper": {"id": "2611.12345", "title": "HF Paper", "summary": "S", "upvotes": 15}}
    parsed_hf = parse_hf_daily_papers([hf_row])
    assert len(parsed_hf) == 1 and parsed_hf[0].votes == 15

    hn_raw = {
        "id": 1,
        "type": "story",
        "title": "Show HN: New Rust CLI",
        "url": "https://github.com/a/b",
        "score": 50,
        "time": 1764000000,
    }
    hn = parse_hn_item(hn_raw)
    assert hn is not None and hn.kind == "tool"

    assert (ROOT / ".github" / "workflows" / "daily.yml").exists()
    assert (ROOT / ".github" / "workflows" / "weekly.yml").exists()
    assert (ROOT / "docs" / "digests.html").exists()
