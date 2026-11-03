"""Тесты ИИ-роутера услуг.

Логика, которую фиксируем:
- когда правила уверены, LLM не зовём вообще (иначе платим секунды зря);
- когда вопрос размытый, арбитром выступает LLM;
- LLM упал или ответил мусором — не роняем ответ, отдаём правила;
- скоуп поиска строится по источникам выбранных услуг.
"""

from app.chat.router import ServiceRouter
from app.models.schemas import Service
from tests.fakes import FakeLlm


def _service(sid: str, title: str, keywords=None, status="проверена", point=None) -> Service:
    return Service(
        id=sid,
        title=title,
        situation="rozhdenie-rebenka",
        summary=title,
        who="",
        amount="",
        deadline="",
        checklist=[],
        sources=[] if point is None else [{"title": "акт", "point": point}],
        keywords=keywords or [],
        status=status,
    )


SERVICES = [
    _service("posobie-rozhdenie", "Единовременное пособие при рождении ребёнка",
             ["пособие при рождении"], point="act_st-11_п-2"),
    _service("posobie-uhod", "Пособие по уходу за ребёнком до 3 лет",
             ["уход за ребенком"], point="act_st-13_п-2"),
]


class RecordingLlm(FakeLlm):
    """LLM-двойка, которая записывает промпты и отвечает по сценарию."""

    def __init__(self, answer=None, max_tokens=None):
        super().__init__()
        self.answer = answer
        self.calls = []
        self.last_max_tokens = None

    def chat(self, system_prompt, user_prompt, max_tokens=None):
        self.calls.append(user_prompt)
        self.last_max_tokens = max_tokens
        return self.answer


def test_rules_confident_do_not_call_llm() -> None:
    llm = RecordingLlm(answer='["posobie-uhod"]')
    router = ServiceRouter(SERVICES, llm=llm)
    ids, by = router.route_with_ai("какие документы для пособия при рождении")
    assert by == "rules"
    assert ids == ["posobie-rozhdenie"]
    assert llm.calls == [], "при уверенных правилах LLM звать нельзя"


def test_ambiguous_question_goes_to_llm() -> None:
    llm = RecordingLlm(answer='["posobie-uhod"]')
    router = ServiceRouter(SERVICES, llm=llm)
    ids, by = router.route_with_ai("сидим с малышом дома, на что рассчитывать")
    assert by == "llm"
    assert ids == ["posobie-uhod"]
    assert len(llm.calls) == 1


def test_router_asks_for_short_answer() -> None:
    """Роутеру хватает ~20 токенов: без лимита арбитр стоит секунды."""
    llm = RecordingLlm(answer='["posobie-uhod"]')
    ServiceRouter(SERVICES, llm=llm).route_with_ai("сидим с малышом дома")
    assert llm.last_max_tokens == 24


def test_llm_garbage_falls_back_to_rules() -> None:
    llm = RecordingLlm(answer="не знаю, наверное что-то про детей")
    router = ServiceRouter(SERVICES, llm=llm)
    ids, by = router.route_with_ai("сидим с малышом дома, на что рассчитывать")
    assert by == "rules"
    assert ids == []


def test_llm_unavailable_falls_back_to_rules() -> None:
    router = ServiceRouter(SERVICES, llm=RecordingLlm(answer=None))
    ids, by = router.route_with_ai("сидим с малышом дома")
    assert by == "rules"
    assert ids == []


def test_llm_ids_are_validated_against_catalog() -> None:
    llm = RecordingLlm(answer='["нет-такой-услуги", "posobie-rozhdenie"]')
    router = ServiceRouter(SERVICES, llm=llm)
    ids, by = router.route_with_ai("сидим с малышом дома, на что рассчитывать")
    assert by == "llm"
    assert ids == ["posobie-rozhdenie"]


def test_json_in_markdown_block_is_parsed() -> None:
    llm = RecordingLlm(answer='```json\n["posobie-uhod"]\n```')
    router = ServiceRouter(SERVICES, llm=llm)
    ids, _ = router.route_with_ai("сидим с малышом дома")
    assert ids == ["posobie-uhod"]


def test_drafts_are_invisible_to_router() -> None:
    services = [*SERVICES, _service("chernovik", "Черновик услуги", ["уход за ребенком"],
                                    status="черновик")]
    router = ServiceRouter(services, llm=RecordingLlm(answer="[]"))
    ids, _ = router.route_with_ai("какие документы для пособия при рождении")
    assert "chernovik" not in ids


def test_scope_chunk_ids_from_service_sources() -> None:
    router = ServiceRouter(SERVICES, llm=RecordingLlm(answer="[]"))
    assert router.scope_chunk_ids(["posobie-rozhdenie"]) == {"act_st-11_п-2"}
    assert router.scope_chunk_ids([]) == set()
    assert router.scope_chunk_ids(["нет-такой"]) == set()


def test_route_without_llm_behaves_like_rules() -> None:
    router = ServiceRouter(SERVICES)
    ids, by = router.route_with_ai("какие документы для пособия при рождении")
    assert by == "rules"
    assert ids == ["posobie-rozhdenie"]
