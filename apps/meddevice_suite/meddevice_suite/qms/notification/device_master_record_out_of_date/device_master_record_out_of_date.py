"""Context for the "Device Master Record Out of Date" alert (module 31): the item's open work orders,
for QA to decide on while the record is out of date."""

from meddevice_suite.qms.device_master_record import open_work_orders


def get_context(context: dict) -> dict:
	return {"open_work_orders": open_work_orders(context["doc"].item)}
