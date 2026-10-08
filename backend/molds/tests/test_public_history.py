from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from molds.models import Machine, MoldAsset, MoldMovement
from molds.services import transition_mold
from .helpers import SeededRackMixin


class PublicMoldRackHistoryTests(SeededRackMixin, TestCase):
    def setUp(self):
        self.source = self.slot("J01", 1, position=1)
        self.target = self.slot("J01", 1, position=2)
        self.mold = self.create_mold("HISTORY-01", self.source)
        self.anonymous = APIClient()

    def history_url(self, slot):
        return f"/api/public/mold-rack/slots/{slot.pk}/history/"

    def test_history_follows_location_instead_of_current_occupant_and_is_minimal(self):
        transition_mold(self.mold, MoldMovement.Action.MOVE, self.user, slot=self.target, note="内部备注")
        other = self.create_mold("HISTORY-02", self.source)
        movement = MoldMovement.objects.create(
            mold=other, action=MoldMovement.Action.CREATE,
            to_status=MoldAsset.Status.IN_STOCK, to_slot=self.source, operator=self.user,
        )
        unrelated = self.create_mold("HISTORY-03", self.slot("J02", 1))
        MoldMovement.objects.create(mold=unrelated, action="EDIT", to_status="IN_STOCK", to_slot=unrelated.current_slot, operator=self.user)
        with self.assertNumQueries(3):
            response = self.anonymous.get(self.history_url(self.source))
        self.assertEqual(response.status_code, 200)
        rows = response.json()["results"]
        self.assertEqual(response.json()["count"], 2)
        self.assertEqual([row["item_code"] for row in rows], [other.asset_code, self.mold.asset_code])
        self.assertEqual(rows[0]["id"], movement.pk)
        self.assertEqual(set(rows[0]), {
            "id", "created_at", "operation_label", "item_code", "item_name",
            "specification", "from_location", "to_location", "from_machine", "to_machine",
        })
        self.assertEqual(rows[1]["from_location"], self.source.display_code)
        self.assertEqual(rows[1]["to_location"], self.target.display_code)
        self.assertEqual(self.anonymous.get(self.history_url(self.target)).json()["count"], 1)

    def test_machine_departure_remains_visible_when_slot_is_empty(self):
        machine = Machine.objects.create(code="MC-3", name="三号机")
        transition_mold(self.mold, MoldMovement.Action.LOAD_MACHINE, self.user, machine=machine)
        detail = self.anonymous.get(f"/api/public/mold-rack/slots/{self.source.pk}/").json()
        self.assertIsNone(detail["mold"])
        row = self.anonymous.get(self.history_url(self.source)).json()["results"][0]
        self.assertEqual(row["from_location"], self.source.display_code)
        self.assertEqual(row["to_machine"], "MC-3")
        self.assertEqual(row["operation_label"], "上机")

    def test_pagination_duplicate_same_slot_records_and_write_protection(self):
        for _ in range(25):
            MoldMovement.objects.create(
                mold=self.mold, action="EDIT", to_status="IN_STOCK",
                from_slot=self.source, to_slot=self.source, operator=self.user,
            )
        MoldMovement.objects.all().update(created_at=timezone.now())
        url = self.history_url(self.source)
        first = self.anonymous.get(url, {"page_size": 1000}).json()
        second = self.anonymous.get(url, {"page": 2}).json()
        self.assertEqual(first["count"], 25)
        self.assertEqual(len(first["results"]), 20)
        self.assertEqual(len(second["results"]), 5)
        self.assertIsNone(second["next"])
        ids = [row["id"] for row in first["results"] + second["results"]]
        self.assertEqual(ids, sorted(set(ids), reverse=True))
        for method in (self.anonymous.post, self.anonymous.patch, self.anonymous.delete):
            self.assertEqual(method(url, {}, format="json").status_code, 405)
        self.assertEqual(MoldMovement.objects.count(), 25)

    def test_empty_slot_and_missing_slot(self):
        self.assertEqual(self.anonymous.get(self.history_url(self.target)).json()["results"], [])
        self.assertEqual(self.anonymous.get("/api/public/mold-rack/slots/999999/history/").status_code, 404)
