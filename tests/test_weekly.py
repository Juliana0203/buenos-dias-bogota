import json
import random
from collections import Counter
from datetime import UTC, date, datetime, timedelta

import pytest

from brief.config import ROOT, load_config
from brief.feedback import (
    add_to_library,
    apply_callback_updates,
    build_piece_keyboard,
    effective_topic_weights,
    load_feedback,
    register_pieces_in_catalog,
)
from brief.sources.tech_sources import (
    TechCandidate,
    fetch_semantic_scholar,
    parse_arxiv_xml,
    parse_hf_daily_papers,
    parse_hn_item,
    parse_tech_rss,
)
from brief.weekly import (
    CONCEPT_GLOSSARY,
    ROLE_ACCESSIBLE,
    ROLE_APPLIED,
    ROLE_DEEP,
    ROLE_GEM,
    ROLE_TOOL,
    _extractive_complete_summary,
    already_sent_this_week,
    build_weekly_challenge,
    build_weekly_digest,
    build_weekly_quiz,
    choose_weekly_format,
    dedupe_candidates,
    format_weekly_messages,
    mark_week_sent,
    parse_week_id,
    score_candidate,
    write_digest_archive,
)

NOW = datetime(2026, 11, 29, 18, 0, tzinfo=UTC)
WCFG = load_config().section("weekly_digest")


def _sample_candidates() -> list[TechCandidate]:
    return [
        TechCandidate(
            title="Quantization of Small Language Models for Rural Classroom Tutoring",
            url="https://arxiv.org/abs/2611.00001",
            source="arXiv",
            kind="paper",
            published=NOW - timedelta(days=1),
            abstract=(
                "We evaluate 4-bit quantization and distillation on a 3B small language model "
                "for offline rural student tutoring via SMS."
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
            title="Tabular Data Cleaning Benchmark for Spreadsheets with Pandas",
            url="https://huggingface.co/papers/2611.00003",
            source="Hugging Face Papers",
            kind="paper",
            published=NOW - timedelta(days=1),
            abstract=(
                "An empirical benchmark and fine-tuning pipeline for data cleaning on spreadsheets "
                "and tabular schemas with pandas."
            ),
            authors=["D. Silva"],
            categories=["cs.LG", "cs.AI"],
            default_topic="ml_applied",
            votes=88,
        ),
        TechCandidate(
            title="AMD Specific Patches for Llama.cpp and GGUF Local Inference",
            url="https://www.technologyreview.com/2026/11/28/llamacpp-amd",
            source="MIT Technology Review",
            kind="news",
            published=NOW - timedelta(days=1),
            abstract=(
                "New llama.cpp patches accelerate GGUF local model inference on standard laptops without dedicated GPU."
            ),
            default_topic="small_models_edge",
            votes=120,
        ),
        TechCandidate(
            title="Colombia lanza portal de datos abiertos geoespaciales para educación rural",
            url="https://example.org/datos-abiertos-colombia-2026",
            source="Reporte LatAm",
            kind="news",
            published=NOW - timedelta(days=2),
            abstract=(
                "El portal publica datasets de datos abiertos en español sobre educación en Bogotá y Colombia."
            ),
            default_topic="open_data_latam",
            language="es",
            votes=65,
        ),
        TechCandidate(
            title="Show HN: PgClean – Python and Postgres CLI for Tabular Data Cleaning",
            url="https://github.com/example/pgclean",
            source="Hacker News",
            kind="tool",
            published=NOW - timedelta(days=1),
            abstract=(
                "Release: PgClean 0.2.0 An open source library in Python and Postgres "
                "to validate and clean tabular datasets .."
            ),
            default_topic="software_engineering",
            votes=210,
        ),
        TechCandidate(
            title="Hardware-Aware Compiler Passes for Low-Power RISC-V Microcontrollers",
            url="https://semanticscholar.org/paper/2611.00009",
            source="Semantic Scholar",
            kind="paper",
            published=NOW - timedelta(days=3),
            abstract="A study on compiler instruction scheduling for embedded microcontrollers.",
            authors=["M. Pardo"],
            categories=["cs.AR"],
            default_topic="software_engineering",
            votes=45,
            citations=4,
        ),
        # Candidatos adicionales de las mismas fuentes para verificar el tope de máx 2 por fuente y 2 por tema
        TechCandidate(
            title="Another arXiv Paper on Bayesian Optimization and Markov Chains",
            url="https://arxiv.org/abs/2611.00010",
            source="arXiv",
            kind="paper",
            published=NOW - timedelta(days=1),
            abstract="Bayesian stochastic convex optimization with Markov chain Monte Carlo.",
            categories=["stat.ML"],
            default_topic="prob_optimization",
            votes=30,
        ),
    ]


def test_max_two_per_source_and_max_two_per_topic_and_titles_intact(tmp_path):
    cfg = load_config()
    cands = _sample_candidates()
    orig_titles = {c.url: c.title for c in cands}
    digest = build_weekly_digest(
        cfg,
        candidates=cands,
        ref_date=date(2026, 11, 29),
        feedback_path=tmp_path / "fb.json",
        sent_path=tmp_path / "sent.json",
    )
    assert len(digest.pieces) == 7

    source_counts = Counter(p.source for p in digest.pieces)
    topic_counts = Counter(p.topic for p in digest.pieces)
    assert max(source_counts.values()) <= 2
    assert max(topic_counts.values()) <= 2

    roles = [p.role for p in digest.pieces]
    assert ROLE_ACCESSIBLE in roles and ROLE_DEEP in roles and ROLE_APPLIED in roles
    assert ROLE_TOOL in roles and ROLE_GEM in roles

    hooks = [p.hook for p in digest.pieces if p.hook]
    assert len(set(hooks)) == len(hooks)

    for p in digest.pieces:
        assert p.original_title == orig_titles[p.url]
        assert not p.original_title.startswith("[")
        if p.kind == "paper":
            assert p.reading_minutes == 0
        if p.summary_es:
            assert p.summary_es.startswith(("[EN] ", "[ES] "))
            assert p.summary_es.endswith((".", "!", "?"))
            assert ".." not in p.summary_es
            assert not p.summary_es[5:].lower().startswith("release:")
            assert "Publicado en Hacker News" not in p.summary_es
            assert len(p.summary_es.split()) <= 25


def test_excerpt_cleaning_strips_repeated_title_and_release_prefix_and_omits_empty():
    cand_with_prefix = TechCandidate(
        title="datasette-charts 0.2a0",
        url="https://simonwillison.net/2026/Nov/24/datasette-charts/",
        source="Simon Willison",
        kind="tool",
        published=NOW,
        abstract="Release: datasette-charts 0.2a0 I introduced this new charting plugin for Datasette yesterday .",
    )
    ext = _extractive_complete_summary(cand_with_prefix, max_words=25)
    assert ext == "[EN] I introduced this new charting plugin for Datasette yesterday."
    assert "Release:" not in ext and ".." not in ext

    # Si el candidato no tiene abstract (como un enlace directo en HN), se omite en vez de poner "Publicado en..."
    cand_empty = TechCandidate(
        title="Minipg: PostgreSQL as a Single Executable",
        url="https://progrium.github.io/minipg/",
        source="Hacker News",
        kind="news",
        published=NOW,
        abstract="",
        votes=90,
    )
    assert _extractive_complete_summary(cand_empty, max_words=25) == ""


def test_llamacpp_scores_high_in_radar_and_physics_is_rejected(tmp_path):
    interests = WCFG["interests"]
    weights = {k: float(v["weight"]) for k, v in interests.items()}

    llamacpp = TechCandidate(
        title="AMD Specific Patches for Llama.cpp",
        url="https://devnull.land/amd-specific-llama-cpp",
        source="Hacker News",
        kind="news",
        published=NOW - timedelta(days=1),
        abstract="",
        votes=56,
    )
    score_llama = score_candidate(llamacpp, interests, weights, NOW, min_affinity=2.0)
    assert score_llama > 0
    assert llamacpp.matched_topic == "small_models_edge"
    assert llamacpp.affinity_score >= 2.0
    assert llamacpp.outside_radar is False

    physics = TechCandidate(
        title="An algebraic quantization of gravity from the null cone",
        url="https://arxiv.org/abs/2611.34924",
        source="arXiv",
        kind="paper",
        published=NOW - timedelta(days=1),
        abstract="We study quantum gravity and general relativity on the null cone in curved spacetime.",
        categories=["gr-qc", "hep-th"],
    )
    assert score_candidate(physics, interests, weights, NOW, min_affinity=2.0) == 0.0


def test_teaser_has_en_tag_without_llm_and_dominant_topic_matches_shown_pieces(tmp_path):
    cfg = load_config()
    digest = build_weekly_digest(
        cfg,
        candidates=_sample_candidates(),
        week_override="2026-W48",
        feedback_path=tmp_path / "fb.json",
        sent_path=tmp_path / "sent.json",
    )
    msgs, markups, silent_flags = format_weekly_messages(digest)
    teaser_lines = msgs[0].splitlines()
    assert len(teaser_lines) == 4
    assert silent_flags == [False, True, True]
    assert "1. [EN] " in teaser_lines[1] or "1. [ES] " in teaser_lines[1]
    assert markups[0] is not None


def test_quiz_randomizes_correct_option_position_and_concepts_are_single_terms():
    for entry in CONCEPT_GLOSSARY:
        assert " y " not in entry["term"].lower()

    concept = CONCEPT_GLOSSARY[0]
    positions = {
        build_weekly_quiz([], concept, rng=random.Random(seed)).correct_option_id
        for seed in range(30)
    }
    assert len(positions) >= 3
    for seed in range(10):
        q = build_weekly_quiz([], concept, rng=random.Random(seed))
        assert len(q.explanation) <= 200
        assert q.options[q.correct_option_id] == concept["quiz_correct"]


def test_challenge_only_proposes_tools_compatible_with_user_stack(tmp_path):
    cfg = load_config()
    digest = build_weekly_digest(
        cfg,
        candidates=_sample_candidates(),
        ref_date=date(2026, 11, 29),
        feedback_path=tmp_path / "fb.json",
        sent_path=tmp_path / "sent.json",
    )
    # PgClean tiene Python y Postgres -> compatible con el stack
    compatible_tool = next(p for p in digest.pieces if p.kind == "tool")
    ch_ok = build_weekly_challenge([compatible_tool])
    assert "compatible con tu stack" in ch_ok and "PgClean" in ch_ok

    # Una herramienta en C++/Zig/browser que NO está en el stack cae al paper (leer abstract y conclusión + 3 notas)
    incompatible_tool = compatible_tool
    incompatible_tool.original_title = "Zig Embedded Kernel Debugger"
    incompatible_tool.summary_es = "[EN] Kernel debugger written in Zig for bare-metal ARM."
    incompatible_tool.hook = ""
    incompatible_tool.url = "https://github.com/example/zig-dbg"
    paper_piece = next(p for p in digest.pieces if p.kind == "paper")
    ch_fallback = build_weekly_challenge([incompatible_tool, paper_piece])
    assert "abstract y la conclusión" in ch_fallback and "3 notas" in ch_fallback


def test_semantic_scholar_429_with_zero_results_raises_as_failed_source(monkeypatch):
    import brief.sources.tech_sources as ts

    def fake_get_json(*_a, **_k):
        raise RuntimeError("429 Too Many Requests")

    monkeypatch.setattr(ts, "get_json", fake_get_json)
    with pytest.raises(RuntimeError, match="Semantic Scholar sin resultados"):
        fetch_semantic_scholar({"enabled": True, "queries": ["test query"]}, api_key="s2_test_key")


def test_dry_run_never_writes_files_or_marks_week_sent(tmp_path, monkeypatch):
    from brief import weekly

    sent_path = tmp_path / "sent.json"
    digests_dir = tmp_path / "digests"
    docs_dir = tmp_path / "docs"
    monkeypatch.setattr(weekly, "SENT_JSON", sent_path)
    monkeypatch.setattr(weekly, "DIGESTS_DIR", digests_dir)
    monkeypatch.setattr(weekly, "DOCS_DIR", docs_dir)
    monkeypatch.setattr(weekly, "gather_tech_candidates", lambda _cfg: _sample_candidates())

    rc = weekly.main(["--dry-run", "--week", "2026-W48"])
    assert rc == 0
    assert not sent_path.exists()
    assert not digests_dir.exists()
    assert not already_sent_this_week("2026-W48", sent_path=sent_path)

    cfg = load_config()
    digest = build_weekly_digest(cfg, candidates=_sample_candidates(), week_override="2026-W48", sent_path=sent_path)
    mark_week_sent(digest, sent_path=sent_path, feedback_path=tmp_path / "fb.json")
    assert already_sent_this_week("2026-W48", sent_path=sent_path)


def test_callback_processing_handles_expired_query_and_edits_reply_markup(tmp_path):
    fb_path = tmp_path / "feedback.json"
    lib_json = tmp_path / "library.json"
    lib_dir = tmp_path / "biblioteca"

    register_pieces_in_catalog(
        [
            {
                "id": "p_1234",
                "title": "Quantization of Small Language Models for Rural Classroom Tutoring",
                "url": "https://arxiv.org/abs/2611.00001",
                "topic": "small_models_edge",
                "topic_label": "Modelos pequeños y edge/offline AI",
                "role": "Paper aplicado",
                "hook": "Relevante por quantization, slm.",
                "summary_es": "[EN] We evaluate 4-bit quantization.",
                "week": "2026-W48",
                "date": "2026-11-29",
            }
        ],
        path=fb_path,
    )

    updates = [
        {
            "update_id": 100,
            "callback_query": {
                "id": "cb1",
                "data": "save:p_1234",
                "message": {"message_id": 55, "chat": {"id": 999}},
            },
        },
        {"update_id": 101, "callback_query": {"id": "cb2", "data": "more:p_1234:small_models_edge"}},
        {"update_id": 102, "callback_query": {"id": "cb3", "data": "less:p_1234:software_engineering"}},
        {"update_id": 103, "callback_query": {"id": "cb4", "data": "read:p_1234"}},
    ]

    def expired_answer(_cid: str, _txt: str) -> None:
        raise RuntimeError("Bad Request: query is too old and response timeout expired")

    edited: list[tuple[int | str, int, dict]] = []
    res1 = apply_callback_updates(
        updates,
        feedback_path=fb_path,
        library_json=lib_json,
        library_dir=lib_dir,
        answer_fn=expired_answer,
        edit_markup_fn=lambda chat_id, msg_id, kb: edited.append((chat_id, msg_id, kb)),
    )
    assert (res1["processed"], res1["saved"], res1["more"], res1["less"], res1["read"]) == (4, 1, 1, 1, 1)
    assert len(edited) == 1
    assert edited[0][2]["inline_keyboard"][0][0]["text"] == "✅ Guardado"

    res2 = apply_callback_updates(updates, feedback_path=fb_path, library_json=lib_json, library_dir=lib_dir)
    assert res2["processed"] == 0 and res2["skipped_duplicate"] == 4

    state = load_feedback(fb_path)
    interests = WCFG["interests"]
    weights = effective_topic_weights(interests, state)
    assert weights["small_models_edge"] > interests["small_models_edge"]["weight"]
    assert weights["software_engineering"] < interests["software_engineering"]["weight"]

    saved_items = json.loads(lib_json.read_text(encoding="utf-8"))
    assert len(saved_items) == 1 and saved_items[0]["id"] == "p_1234"
    assert not add_to_library(saved_items[0], library_json=lib_json, library_dir=lib_dir)


def test_archive_and_parsers_and_format_rotation(tmp_path):
    idx1, fmt1 = choose_weekly_format(48, last_format_index=None)
    idx2, fmt2 = choose_weekly_format(48, last_format_index=idx1)
    assert fmt1 != fmt2

    c1 = _sample_candidates()[0]
    c2 = TechCandidate(
        title="Quantization of Small Language Models for Rural Classroom Tutoring!",
        url="https://huggingface.co/papers/2611.00001",
        source="Hugging Face Papers",
        kind="paper",
        published=NOW,
        abstract=c1.abstract,
        score=10.0,
    )
    c1.score = 12.0
    assert len(dedupe_candidates([c1, c2], 78.0)) == 1

    cfg = load_config()
    digest = build_weekly_digest(
        cfg,
        candidates=_sample_candidates(),
        week_override="2026-W48",
        feedback_path=tmp_path / "fb.json",
        sent_path=tmp_path / "sent.json",
    )
    md_path = write_digest_archive(digest, digests_dir=tmp_path / "digests", docs_dir=tmp_path / "docs")
    assert md_path.exists()

    y, w, sun = parse_week_id("2026-W48")
    assert (y, w, sun.weekday()) == (2026, 48, 6)
    kb = build_piece_keyboard("p_1", "edtech", saved=True)
    assert kb["inline_keyboard"][0][0]["text"] == "✅ Guardado"

    rss = b"""<?xml version="1.0"?><rss version="2.0"><channel>
    <item><title>Recent AI Post</title><link>https://hf.co/blog/recent</link>
    <pubDate>Sat, 28 Nov 2026 10:00:00 GMT</pubDate><description>Recent post.</description></item>
    <item><title>Old 2024 AI Post</title><link>https://hf.co/blog/old</link>
    <pubDate>Mon, 01 Jan 2024 10:00:00 GMT</pubDate><description>Old post.</description></item>
    </channel></rss>"""
    items = parse_tech_rss(rss, {"name": "HF Blog", "topic": "ml_applied"}, now=NOW, max_age_days=7.0, max_items=5)
    assert len(items) == 1

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
    assert len(parse_arxiv_xml(xml, now=NOW)) == 1
    hf_row = {"paper": {"id": "2611.12345", "title": "HF Paper", "summary": "S", "upvotes": 15}}
    assert len(parse_hf_daily_papers([hf_row], now=NOW)) == 1
    hn_raw = {
        "id": 1,
        "type": "story",
        "title": "Show HN: New Rust CLI",
        "url": "https://github.com/a/b",
        "score": 50,
        "time": int(NOW.timestamp()) - 3600,
    }
    hn = parse_hn_item(hn_raw, now=NOW)
    assert hn is not None and hn.kind == "tool"
    assert (ROOT / ".github" / "workflows" / "daily.yml").exists()
    assert (ROOT / ".github" / "workflows" / "weekly.yml").exists()
