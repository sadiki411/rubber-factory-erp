"""Run ONLY in a disposable Linux container with /probe dedicated test storage.

create: fresh synthetic DB/PDF/photo then backup_erp; read: after container restart;
restore: backup_erp into /probe/restored only, then verify identical private bytes.
No production mount, HTTP listener, external network or production credentials.
"""
import base64
import hashlib
import io
import json
import os
import sys
from pathlib import Path

phase = sys.argv[1]
assert phase in {"create", "read", "restore"}
root = Path("/probe")
assert root.is_dir() and os.environ.get("FLOW_REVIEW_DISPOSABLE") == "YES"
runtime = root / ("restored" if phase == "restore" else "runtime")
runtime.mkdir(exist_ok=True)
os.environ.update(SQLITE_PATH=str(runtime / "db.sqlite3"), MEDIA_ROOT=str(runtime / "media"),
    FLOW_CARDS_PRIVATE_ROOT=str(runtime / "private"), FLOW_CARDS_WRITE_ENABLED="1",
    DJANGO_DEBUG="0", DJANGO_ALLOWED_HOSTS="testserver,localhost", DJANGO_SETTINGS_MODULE="erp.settings")
sys.path.insert(0, "/app/backend")
import django
django.setup()
from django.core.management import call_command
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient
from orders.models import ProductSpecification
from quality.models import QualityOrder
from flowcards import services as s
from flowcards.models import InternalCard, AppearanceIssue
from flowcards.pdf import font_name

def content(response):
    assert response.status_code == 200, (response.status_code, getattr(response, "content", b""))
    raw = b"".join(response.streaming_content) if response.streaming else response.content
    response.close()
    return raw

if phase == "create":
    assert not (runtime / "db.sqlite3").exists(), "Existing DB: refusing create"
    call_command("migrate", interactive=False, verbosity=0)
    user = get_user_model().objects.create_user("linux-disposable")
    client = APIClient(); client.force_authenticate(user)
    entries = []
    for tag, whole, long in [("required", "REQUIRED", False), ("optional", "NOT_REQUIRED", False), ("long", "REQUIRED", True)]:
        product = ProductSpecification.objects.create(specification="Linux中文长字段-"*16 if long else "Linux中文样品", material="隔离示例")
        order = QualityOrder.objects.create(order_no="LINUX-"+tag, product_specification=product, specification=product.specification,
            material=product.material, source_system="翔邦", order_quantity=1000, created_by=user)
        st = s.confirm_standard({"request_key": "standard-"+tag, "product_id": product.pk, "source": "隔离Linux要求→快照→PDF链",
            "appearance_ids": list(AppearanceIssue.objects.values_list("pk",flat=True))[:4], "dimension_count": 0,
            "bake": {"state":"APPLICABLE", "temperature_lower":150,"temperature_upper":160,"minutes_lower":120,"minutes_upper":150}}, user)
        group = s.generate_group(s.create_group({"request_key":"group-"+tag,"product_id":product.pk,"order_ids":[order.pk],"standard_id":st.pk,
            "planned_molds":100,"whole_mold":whole,"whole_mold_basis":"隔离确认","plan_basis":"隔离100模"},user),user)
        card = group.cards.get()
        response = client.post("/api/flow-cards/print-jobs/",{"request_key":"print-"+tag,"card_ids":[str(card.pk)]},format="json")
        assert response.status_code == 201, response.content
        url = response.json()["pdf_url"]; raw = content(client.get(url))
        assert raw.startswith(b"%PDF"), "PDF signature"
        (root / (tag+".pdf")).write_bytes(raw)
        entries.append({"tag":tag,"url":url,"sha256":hashlib.sha256(raw).hexdigest(),"card_id":str(card.pk)})
    from PIL import Image
    image = io.BytesIO(); Image.new("RGB",(20,20),"white").save(image,"PNG")
    response = client.post("/api/flow-cards/attachments/",{"card_id":entries[0]["card_id"],"file":SimpleUploadedFile("original.png",image.getvalue(),content_type="image/png")},format="multipart")
    assert response.status_code == 201, response.content
    url=f"/api/flow-cards/attachments/{response.json()['id']}/content/"
    entries.append({"tag":"original","url":url,"sha256":hashlib.sha256(content(client.get(url))).hexdigest()})
    (root/"expected.json").write_text(json.dumps(entries,ensure_ascii=False,indent=2),encoding="utf-8")
    call_command("backup_erp", output=str(root/"backups"), retention_count=2, verbosity=0)
elif phase == "restore":
    assert not (runtime / "db.sqlite3").exists(), "Refusing to overwrite even isolated prior restore"
    archive = next((root/"backups").glob("*.zip"))
    call_command("backup_erp", restore=str(archive), force=True, retention_count=2, verbosity=0)

user = get_user_model().objects.get(username="linux-disposable")
client = APIClient(); client.force_authenticate(user)
anonymous = APIClient()
checks=[]
for item in json.loads((root/"expected.json").read_text(encoding="utf-8")):
    raw=content(client.get(item["url"]))
    assert hashlib.sha256(raw).hexdigest()==item["sha256"], "Frozen original differs"
    response=anonymous.get(item["url"])
    assert response.status_code==403, response.status_code
    checks.append({**item,"authenticated":200,"anonymous":403,"bytes":len(raw)})
result={"phase":phase,"sha":os.environ.get("FLOW_REVIEW_SHA"),"font":font_name(),"uid":os.getuid(),"checks":checks,
    "synthetic_only":True,"network":"none","production_mounts":False}
(root/(phase+"-result.json")).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(result,ensure_ascii=False))
