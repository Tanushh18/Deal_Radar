from types import SimpleNamespace

from app.services import replica


def req(method, path):
    return SimpleNamespace(method=method, url=SimpleNamespace(path=path))


def test_reads_stay_local_everything_else_forwards():
    assert replica.serves_locally(req("GET", "/api/deals"))
    assert replica.serves_locally(req("GET", "/api/deals/abc/history"))
    assert replica.serves_locally(req("GET", "/api/ping"))
    assert replica.serves_locally(req("GET", "/"))
    assert not replica.serves_locally(req("POST", "/api/deals/abc/coupon-dead"))
    assert not replica.serves_locally(req("GET", "/api/deals/abc/go"))
    assert not replica.serves_locally(req("GET", "/api/channels"))
    assert not replica.serves_locally(req("POST", "/api/auth/login"))
