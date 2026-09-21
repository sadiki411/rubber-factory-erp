from django.core.management.base import BaseCommand

from inventory.services import bootstrap_inventory_locations


class Command(BaseCommand):
    help = "初始化成品库存固定货架库位（K01-K09及冰箱）"

    def handle(self, *args, **options):
        created = bootstrap_inventory_locations()
        self.stdout.write(self.style.SUCCESS(f"库存库位初始化完成，新增 {created} 个库位。"))

