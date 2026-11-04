"""Conservative identities for new automatic links; never rewrite history."""


def order_customer_key(order):
    # source_system is often a supplier name, NOT a customer field. Only
    # explicit customer names and recognized customer document types count.
    keys = set()
    source = str(order.source_system or "").strip().casefold()
    if source in {"翔邦", "艾斯霖"}:
        keys.add(source)
    for batch in (order.source_batch, order.last_source_batch):
        if batch is None:
            continue
        if batch.source_type == "FACTORY_WORK_CONTACT":
            keys.add("翔邦")
        elif batch.source_type == "PURCHASE_ORDER_IMAGES":
            keys.add("艾斯霖")
    return next(iter(keys)) if len(keys) == 1 else None


def can_auto_allocate_orders(source, target):
    customer = order_customer_key(source)
    return bool(
        customer
        and customer == order_customer_key(target)
        and source.product_specification_id
        and source.product_specification_id == target.product_specification_id
        and str(source.specification or "").strip().casefold()
        == str(target.specification or "").strip().casefold()
        and str(source.material or "").strip().casefold()
        == str(target.material or "").strip().casefold()
    )
