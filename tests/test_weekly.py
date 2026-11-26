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
from brief.sources.tech_sources import (
    TechCandidate,
    parse_arxiv_xml,
    parse_hf_daily_papers,
    parse_hn_item,
    parse_tech_rss,
)
from brief.weekly import (
    ROLE_ACCESSIBLE,
    ROLE_APPLIED,
    ROLE_DEEP,
    ROLE_GEM,
    ROLE_NEWS,
    ROLE_TOOL,
    already_sent_this_week,
    build_weekly_challenge,
    build_weekly_digest,
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
            title="Quantization of Small Language Models for Edge Classroom Tutoring",
            url="https://arxiv.org/abs/2611.00001",
            source="arXiv",
            kind="paper",
            published=NOW - timedelta(days=1),
            abstract=(
                "We evaluate 4-bit quantization and distillation on a 3B small language model "
                "for offline student tutoring and assessment. Results show 3.8x memory reduction on laptops."
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
                "Markov chain Monte Carlo under non-convex objectives. The bound holds in finite samples."
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
                "and schemas from noisy PDF documents with low latency. Evaluated on 12 enterprise datasets."
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
                "Researchers released a compact 3B multimodal model with GGUF quantization "
                "for local inference on standard hardware. It supports vision and edge deployment."
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
                "El portal integra datasets de datos abiertos sobre movilidad, educación "
                "y contratación pública en Bogotá, Colombia y Latinoamérica."
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
                "to validate, query and test SQLite and Postgres tabular datasets."
            ),
            default_topic="software_engineering",
            votes=210,
        ),
        TechCandidate(
            title="Hardware-Aware Compiler Passes for Low-Power RISC-V Microcontrollers",
            url="https://arxiv.org/abs/2611.00009",
            source="Semantic Scholar",
            kind="paper",
            published=NOW - timedelta(days=3),
            abstract=(
                "A study on compiler instruction scheduling for embedded microcontrollers. "
                "Reduces cycle count by 14% across embedded benchmarks."
            ),
            authors=["M. Pardo"],
            categories=["cs.AR"],
            default_topic="software_engineering",
            votes=45,
            citations=4,
        ),
    ]


def test_titles_are_never_altered_and_hooks_are_unique_and_summaries_complete(tmp_path):
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

    roles = [p.role for p in digest.pieces]
    assert ROLE_ACCESSIBLE in roles
    assert ROLE_DEEP in roles
    assert ROLE_APPLIED in roles
    assert roles.count(ROLE_NEWS) == 2
    assert ROLE_TOOL in roles
    assert ROLE_GEM in roles

    hooks = [p.hook for p in digest.pieces]
    # Dos piezas distintas NUNCA comparten la misma frase de gancho / "por qué importa"
    assert len(set(hooks)) == len(hooks)

    for p in digest.pieces:
        # El título original se conserva intacto sin prefijos [Tema] ni traducciones parciales
        assert p.original_title == orig_titles[p.url]
        assert not p.original_title.startswith("[")
        assert "cuantización" not in p.original_title
        # Resumen extractivo: <= 40 palabras, empieza con [EN] o [ES], termina en oración completa
        assert p.summary_es.startswith(("[EN] ", "[ES] "))
        assert p.summary_es.endswith((".", "!", "?"))
        assert len(p.summary_es.split()) <= 40


def test_physics_paper_is_rejected_and_outside_radar_only_allowed_as_gem(tmp_path):
    interests = WCFG["interests"]
    weights = {k: float(v["weight"]) for k, v in interests.items()}

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

    # El candidato 7 de _sample_candidates tiene 1 sola keyword ("compiler", afinidad 1.3 < 2.0):
    # queda marcado outside_radar=True y solo entra como ROLE_GEM con "fuera de tu radar habitual"
    cfg = load_config()
    cands = [*_sample_candidates(), physics]
    digest = build_weekly_digest(
        cfg,
        candidates=cands,
        ref_date=date(2026, 11, 29),
        feedback_path=tmp_path / "fb.json",
        sent_path=tmp_path / "sent.json",
    )
    urls = {p.url for p in digest.pieces}
    assert physics.url not in urls

    non_gem = [p for p in digest.pieces if p.role != ROLE_GEM]
    assert all(not p.outside_radar for p in non_gem)
    gem = next(p for p in digest.pieces if p.role == ROLE_GEM)
    assert gem.outside_radar is True
    _, msgs_body, _ = format_weekly_messages(digest)
    full_html = "\n".join(format_weekly_messages(digest)[0])
    assert "fuera de tu radar habitual" in full_html


def test_project_connection_only_when_concrete_keywords_match(tmp_path):
    cfg = load_config()
    digest = build_weekly_digest(
        cfg,
        candidates=_sample_candidates(),
        ref_date=date(2026, 11, 29),
        feedback_path=tmp_path / "fb.json",
        sent_path=tmp_path / "sent.json",
    )
    by_url = {p.url: p for p in digest.pieces}
    # Paper 1 (classroom, tutoring, student) -> Aula Ya
    assert "Aula Ya" in by_url["https://arxiv.org/abs/2611.00001"].project_line
    # Paper 3 (tabular, tables, schemas, PDF) -> Table Master
    assert "Table Master" in by_url["https://arxiv.org/abs/2611.00003"].project_line
    # Paper 2 (teoría pura de Markov chain Monte Carlo sin keywords de Aula Ya / Table Master / PostMark) -> vacío
    assert by_url["https://arxiv.org/abs/2611.00002"].project_line == ""


def test_challenge_is_coherent_with_piece_kind(tmp_path):
    cfg = load_config()
    digest = build_weekly_digest(
        cfg,
        candidates=_sample_candidates(),
        ref_date=date(2026, 11, 29),
        feedback_path=tmp_path / "fb.json",
        sent_path=tmp_path / "sent.json",
    )
    tool_piece = next(p for p in digest.pieces if p.kind == "tool")
    paper_piece = next(p for p in digest.pieces if p.kind == "paper")
    news_piece = next(p for p in digest.pieces if p.kind == "news")

    ch_tool = build_weekly_challenge([tool_piece])
    assert "20 min" in ch_tool and "prueba" in ch_tool.lower()

    ch_paper = build_weekly_challenge([paper_piece])
    assert "abstract" in ch_paper.lower() and "conclusión" in ch_paper.lower() and "3 notas" in ch_paper.lower()

    ch_news = build_weekly_challenge([news_piece])
    assert "cómo afecta" in ch_news.lower() and "clona" not in ch_news.lower()


def test_first_message_is_4_line_teaser_and_only_one_with_sound(tmp_path):
    cfg = load_config()
    digest = build_weekly_digest(
        cfg,
        candidates=_sample_candidates(),
        week_override="2026-W48",
        feedback_path=tmp_path / "fb.json",
        sent_path=tmp_path / "sent.json",
    )
    msgs, markups, silent_flags = format_weekly_messages(digest)
    assert len(msgs) == 3
    assert len(msgs[0].splitlines()) <= 4
    assert silent_flags == [False, True, True]
    assert all(len(m) <= 4096 for m in msgs)
    assert markups[0] is not None and "inline_keyboard" in markups[0]


def test_rss_and_sources_filter_to_7_days_and_limit_per_source():
    rss = b"""<?xml version="1.0"?><rss version="2.0"><channel>
    <item><title>Recent AI Post</title><link>https://hf.co/blog/recent</link>
    <pubDate>Sat, 28 Nov 2026 10:00:00 GMT</pubDate><description>Recent post.</description></item>
    <item><title>Old 2024 AI Post</title><link>https://hf.co/blog/old</link>
    <pubDate>Mon, 01 Jan 2024 10:00:00 GMT</pubDate><description>Old post.</description></item>
    </channel></rss>"""
    items = parse_tech_rss(rss, {"name": "HF Blog", "topic": "ml_applied"}, now=NOW, max_age_days=7.0, max_items=5)
    assert len(items) == 1 and items[0].title == "Recent AI Post"


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

    # Cuando sí se registra un envío real, se guarda en sent.json
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
                "title": "Quantization of Small Language Models for Edge Classroom Tutoring",
                "url": "https://arxiv.org/abs/2611.00001",
                "topic": "small_models_edge",
                "topic_label": "Modelos pequeños y edge/offline AI",
                "role": "Paper aplicado",
                "hook": "Results show 3.8x memory reduction on laptops.",
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
    assert edited[0][0] == 999 and edited[0][1] == 55
    first_btn_text = edited[0][2]["inline_keyboard"][0][0]["text"]
    assert first_btn_text == "✅ Guardado"

    # Reenviar los mismos updates no los procesa de nuevo
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
        title="Quantization of Small Language Models for Edge Classroom Tutoring!",
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
