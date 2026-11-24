from src.domain.analysis import wrap
from src.services import reconcile as service


def reconcile(check_rows, departure_rows, params):
    return wrap(service.reconcile(check_rows, departure_rows, params))
