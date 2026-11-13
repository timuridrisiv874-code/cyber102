"""Shared assessment with an explicit boundary around validated demo rules."""
from . import customer, guidance, recommendations
from .diagnostics import evaluate
from .demo_data import PASSPORT


def diagnose(passport, telemetry, vision):
    # The supplied AMS passports describe a different pump family. A serial
    # match alone is not validation of the centrifugal-pump demonstration rules.
    applicable = passport.get('serial') == PASSPORT['serial'] and passport.get('source') == PASSPORT['source']
    if not applicable:
        return {'findings': [], 'guidance': {
            'applicable': False, 'rules': [], 'nextChecks': [],
            'summary': {'total': 0, 'triggered': 0, 'notTriggered': 0, 'insufficientData': 0, 'assessed': 0},
            'reason': 'Правила демонстрационного P-102 не валидированы для этого оборудования. '
                      'Сохраните наблюдения и сверяйтесь с паспортом выбранного узла.'}}
    thresholds = passport.get('thresholds', {})
    return {'findings': evaluate(telemetry, vision, thresholds),
            'guidance': {'applicable': True, **guidance.assess(telemetry, vision, thresholds)}}


def references(serial, component=None, rule_ids=None, root=customer.ROOT):
    try:
        return recommendations.for_equipment(serial, component, rule_ids, root=root)
    except (FileNotFoundError, ValueError, KeyError, OSError):
        return {'equipment': None, 'applicability': 'unavailable', 'recommendations': [],
                'availableComponents': [], 'limitations': ['Каталог паспортов временно недоступен.']}
