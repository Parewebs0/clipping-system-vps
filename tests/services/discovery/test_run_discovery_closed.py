"""run_discovery must drop campaigns whose fetch_detail returned None
(closed on the source) and keep the ones whose fetch_detail raised."""
import unittest
import unittest.mock as mock

from app.services.discovery.models import DiscoveredCampaign
from app.services.discovery.upsert import run_discovery


def _dc(ext):
    return DiscoveredCampaign(
        name=f"camp {ext}", provider="whop", external_id=ext,
        detail_url=f"https://whop.com/x/{ext}", raw={},
    )


class _FakeProvider:
    name = "whop"

    def discover(self, *, limit=50):
        return [_dc("open"), _dc("closed"), _dc("boom")]

    def fetch_detail(self, d):
        if d.external_id == "closed":
            return None
        if d.external_id == "boom":
            raise RuntimeError("network")
        return d


class TestRunDiscoveryClosed(unittest.TestCase):
    def test_closed_dropped_errors_kept(self):
        upserted = []

        def fake_upsert(db, d, **kw):
            upserted.append(d.external_id)
            return mock.Mock(id=1)

        with mock.patch("app.services.discovery.registry.all_providers",
                        return_value=[_FakeProvider()]), \
             mock.patch("app.services.discovery.upsert.upsert_campaign",
                        side_effect=fake_upsert):
            summary = run_discovery(mock.Mock(), fetch_detail=True, limit=10)

        self.assertEqual(sorted(upserted), ["boom", "open"])
        self.assertEqual(summary["providers"]["whop"]["discovered"], 2)


if __name__ == "__main__":
    unittest.main()
