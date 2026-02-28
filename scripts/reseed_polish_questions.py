import os
import sys
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import django

django.setup()

from surveys.models import Question, QuestionChoice, SurveyTemplate, TemplateNode


def mkq(title, qtype, options=None, required=True):
    question = Question.objects.create(title=title, question_type=qtype, required=required)
    for index, option in enumerate(options or [], start=1):
        QuestionChoice.objects.create(question=question, label=option, order=index)
    return question


def main():
    Question.objects.all().delete()
    SurveyTemplate.objects.all().delete()

    yn = Question.QuestionType.YES_NO
    mc = Question.QuestionType.MULTI_CHOICE
    op = Question.QuestionType.OPEN

    q = {}
    q["q1"] = mkq("Nazwa firmy", op)
    q["q2"] = mkq(
        "Forma organizacyjna",
        mc,
        [
            "Spółdzielnia Mieszkaniowa",
            "Wspólnota Mieszkaniowa",
            "Firma Zarządzająca Nieruchomościami",
            "Inne",
        ],
    )
    q["q3"] = mkq("Osoba kontaktowa", op)
    q["q4"] = mkq("Telefon kontaktowy", op)
    q["q5"] = mkq("E-mail", op)
    q["q6"] = mkq("WWW", op, required=False)
    q["q7"] = mkq("Czy obiekt jest objęty monitoringiem wizyjnym?", yn)
    q["q8"] = mkq("Jakie kamery funkcjonują obecnie na obiekcie?", mc, ["Analogowe", "IP", "System hybrydowy", "Nie wiem"])
    q["q9"] = mkq("Liczba kamer", op)
    q["q10"] = mkq(
        "Jakie obszary są objęte monitoringiem?",
        mc,
        [
            "Wejścia do klatek schodowych / parter",
            "Kamery na pozostałych piętrach",
            "Parking naziemny / garaże",
            "Parking podziemny",
            "Strefy wjazdowe i wejściowe",
            "Piwnice",
            "Windy",
            "Tereny zielone",
            "Śmietniki",
        ],
    )
    q["q11"] = mkq("Czy są miejsca wymagające monitoringu, które obecnie nie są objęte kamerami?", yn)
    q["q12"] = mkq("Jakie to obszary? (jeśli TAK)", op, required=False)
    q["q13"] = mkq("Czy obraz jest nagrywany w sposób ciągły czy tylko przy wykryciu ruchu?", mc, ["W sposób ciągły", "Po wykryciu ruchu"])
    q["q14"] = mkq("Jak długo przechowywane są nagrania (dni)?", op)
    q["q15"] = mkq("Czy mają Państwo zdalny dostęp do podglądu z kamer?", yn)
    q["q16"] = mkq("Od ilu lat orientacyjnie działa system?", op)
    q["q17"] = mkq("Czy wszystkie kamery działają prawidłowo?", yn)
    q["q18"] = mkq("Opisz problemy (jeśli NIE)", op, required=False)
    q["q19"] = mkq(
        "Jak często system jest serwisowany?",
        mc,
        ["Raz w miesiącu", "Co 2 miesiące", "Co kwartał", "Raz na pół roku", "Raz w roku", "Tylko przy problemach", "Wcale", "Nie wiem"],
    )
    q["q20"] = mkq("Jak bardzo jesteście Państwo zadowoleni z jakości serwisu? (1-10)", op)
    q["q21"] = mkq("Czy obiekt jest oznakowany? (tabliczki CCTV)", yn)
    q["q22"] = mkq("Czy posiadają Państwo wymagane dokumenty dot. monitoringu i RODO?", yn)
    q["q23"] = mkq("Czy posiadają Państwo wyznaczonego Administratora systemu CCTV?", yn)
    q["q24"] = mkq("Czy byliby Państwo zainteresowani audytem technicznym i prawnym?", yn)
    q["q25"] = mkq("Czy obecny monitoring spełnia Państwa oczekiwania?", yn)
    q["q26"] = mkq("Jakich usprawnień Państwo potrzebujecie?", mc, ["Jakość obrazu", "Ilość kamer", "Zasięg kamer", "Dostęp online", "Archiwizacja", "RODO"])
    q["q27"] = mkq(
        "Z jakiego powodu obiekt nie jest objęty monitoringiem?",
        mc,
        ["Brak potrzeby", "Koszty instalacji", "Kwestie prywatności / RODO", "Brak decyzji wspólnoty", "Planowana instalacja", "Inne"],
    )
    q["q28"] = mkq("Czy na terenie obiektu dochodziło do incydentów?", mc, ["Tak, regularnie", "Sporadycznie", "Nie", "Trudno powiedzieć"])
    q["q29"] = mkq(
        "Które obszary są najbardziej narażone na incydenty?",
        mc,
        ["Wejścia", "Garaże podziemne", "Parkingi zewnętrzne", "Klatki schodowe", "Windy", "Tereny wokół budynków"],
    )
    q["q30"] = mkq('Gdzie znajduje się "serce" systemu?', mc, ["Portiernia", "Wydzielone pomieszczenie", "Kotłownia"])
    q["q31"] = mkq("Czy rozważają Państwo instalację monitoringu?", mc, ["Do 3 miesięcy", "Do pół roku", "Jak najszybciej", "Raczej nie"])
    q["q32"] = mkq("Bariery decyzyjne", op, required=False)
    q["q33"] = mkq("Jakie adresy obejmuje obiekt?", op)
    q["q34"] = mkq("Czy na terenie obiektu zapewniona jest całodobowa ochrona fizyczna (24/7)?", yn)
    q["q35"] = mkq("Czy w budynku dostępne jest stałe łącze internetowe?", yn)
    q["q36"] = mkq(
        "Czy byliby Państwo zainteresowani innymi rozwiązaniami bezpieczeństwa?",
        mc,
        ["Kontrola dostępu", "Alarmy", "Domofony / Wideofony", "Bramy i szlabany", "Systemy przeciwpożarowe", "Sieci strukturalne", "Elektryka"],
    )
    q["q37"] = mkq("Prosimy o wskazanie szczegółowych potrzeb lub oczekiwań", op, required=False)
    q["q38"] = mkq("Czy chcą Państwo otrzymać informacje o integracji z Całodobowym Centrum Monitoringu?", yn)

    template = SurveyTemplate.objects.create(name="Analiza Potrzeb Klienta CCTV", description="Struktura oparta o plik Excel")
    nodes = {}
    for i in range(1, 39):
        key = f"q{i}"
        x = 100 + ((i - 1) % 6) * 260
        y = 80 + ((i - 1) // 6) * 150
        nodes[key] = TemplateNode.objects.create(template=template, question=q[key], x=x, y=y)

    def nxt(a, b):
        nodes[a].next_node = nodes[b]

    def y(a, b):
        nodes[a].yes_node = nodes[b]

    def n(a, b):
        nodes[a].no_node = nodes[b]

    nxt("q1", "q2")
    nxt("q2", "q3")
    nxt("q3", "q4")
    nxt("q4", "q5")
    nxt("q5", "q6")
    nxt("q6", "q7")
    y("q7", "q8")
    n("q7", "q27")
    nxt("q8", "q9")
    nxt("q9", "q10")
    nxt("q10", "q11")
    y("q11", "q12")
    n("q11", "q13")
    nxt("q12", "q13")
    nxt("q13", "q14")
    nxt("q14", "q15")
    nxt("q15", "q16")
    nxt("q16", "q17")
    y("q17", "q19")
    n("q17", "q18")
    nxt("q18", "q19")
    nxt("q19", "q20")
    nxt("q20", "q21")
    nxt("q21", "q22")
    nxt("q22", "q23")
    nxt("q23", "q24")
    nxt("q24", "q25")
    y("q25", "q33")
    n("q25", "q26")
    nxt("q26", "q33")
    nxt("q27", "q28")
    nxt("q28", "q29")
    nxt("q29", "q30")
    nxt("q30", "q31")
    nxt("q31", "q32")
    nxt("q32", "q33")
    nxt("q33", "q34")
    nxt("q34", "q35")
    nxt("q35", "q36")
    nxt("q36", "q37")
    nxt("q37", "q38")

    for node in nodes.values():
        node.save()

    template.start_node = nodes["q1"]
    template.save(update_fields=["start_node", "updated_at"])

    print("questions", Question.objects.count())
    print("templates", SurveyTemplate.objects.count())
    print("nodes", TemplateNode.objects.count())


if __name__ == "__main__":
    main()
