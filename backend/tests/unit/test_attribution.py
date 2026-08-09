"""Attribution gate — the fix for the failure the live evaluation caught."""

from __future__ import annotations

import pytest

from app.clients.vectorstore import Chunk
from app.rag.attribution import check

pytestmark = pytest.mark.unit


def _chunk(text: str, name: str = "handbook.md") -> Chunk:
    return Chunk(
        chunk_id="c1",
        document_id="d1",
        document_name=name,
        text=text,
        score=0.9,
        page_number=1,
        source_uri="s3://x",
        document_version=1,
        tenant_id="t",
        department="hr",
    )


HANDBOOK = _chunk(
    "Acme HR Handbook. Employees with 12 months of service receive 18 weeks of "
    "parental leave at full pay."
)


def test_unknown_organisation_is_blocked():
    """The exact live-eval failure: answering about Globex from the Acme handbook."""
    result = check(
        "What does the Globex handbook say about parental leave?", [HANDBOOK]
    )
    assert result.blocked
    assert "Globex" in result.unknown_entities


def test_known_organisation_passes():
    result = check("What does the Acme handbook say about parental leave?", [HANDBOOK])
    assert result.ok


def test_plain_question_passes():
    result = check("How many weeks of parental leave do employees get?", [HANDBOOK])
    assert result.ok


@pytest.mark.parametrize(
    "question",
    [
        "Combien de semaines de conge parental?",
        "Cuantos dias de vacaciones anuales?",
        "Quantas semanas de licenca parental?",
        "Wieviele Wochen Elternzeit gibt es?",
    ],
)
def test_sentence_initial_capitals_are_not_entities(question):
    """A capital at the start of a sentence is a question word, not an organisation."""
    assert check(question, [HANDBOOK]).ok, f"false refusal on {question!r}"


def test_common_words_never_trigger():
    for q in [
        "What is the HR policy?",
        "Show me the CFO approval limit.",
        "Which USD threshold applies?",
        "According to the handbook, how much leave?",
    ]:
        assert check(q, [HANDBOOK]).ok, f"false refusal on {q!r}"


def test_no_chunks_is_not_blocked():
    """With no evidence the refusal comes from elsewhere; this gate stays quiet."""
    assert check("What does Globex say?", []).ok


def test_entity_present_in_document_name_passes():
    doc = _chunk("Some content about leave.", name="Globex-Handbook.pdf")
    assert check("What does the Globex handbook say?", [doc]).ok


def test_possessive_form_blocked():
    result = check("What is Globex's parental leave entitlement?", [HANDBOOK])
    assert result.blocked
    assert "Globex" in result.unknown_entities


def test_according_to_form_blocked():
    result = check("According to Initech, how much leave is granted?", [HANDBOOK])
    assert result.blocked


def test_generic_qualifiers_are_not_entities():
    """ "the company policy", "our handbook" must not be treated as unknown sources."""
    for q in [
        "What does the company policy say about leave?",
        "What does our handbook say about leave?",
        "What does the HR policy say about leave?",
        "What does the current policy say?",
    ]:
        assert check(q, [HANDBOOK]).ok, f"false refusal on {q!r}"


def test_german_nouns_do_not_false_refuse():
    """German capitalises every noun; capitalisation alone must never trigger the gate."""
    for q in [
        "Wieviele Wochen Elternzeit gibt es?",
        "Was sagt das Handbuch uber Urlaub?",
        "Wie viele Tage Jahresurlaub?",
    ]:
        assert check(q, [HANDBOOK]).ok, f"false refusal on {q!r}"
