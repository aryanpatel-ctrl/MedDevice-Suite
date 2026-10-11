"""Install and migrate entry point.

Everything this app configures on a site is applied from here, so a fresh
install and every ``bench migrate`` converge on the same declared state.
Each step must be idempotent.
"""

from meddevice_suite.compliance.alerts import apply_alerts
from meddevice_suite.compliance.archive import ensure_indexes as apply_archive_indexes
from meddevice_suite.compliance.esign import apply_lockout
from meddevice_suite.compliance.retention import apply_retention
from meddevice_suite.lot_control.lot_status import apply_lot_status
from meddevice_suite.lot_control.receipt import apply_receipt_fields
from meddevice_suite.lot_control.stores import apply_store_roles
from meddevice_suite.master_data.customer_licence import apply_customer_licence_fields
from meddevice_suite.master_data.equipment import apply_equipment_fields
from meddevice_suite.master_data.item_master import apply_item_master_fields
from meddevice_suite.master_data.routing import apply_routing_fields
from meddevice_suite.master_data.specification import apply_specification_fields
from meddevice_suite.master_data.supplier_approval import apply_supplier_approval_fields
from meddevice_suite.master_data.versioning import apply_master_versions
from meddevice_suite.master_data.warehouse import apply_warehouse_fields
from meddevice_suite.setup.desk import apply_desk_configuration
from meddevice_suite.setup.roles import apply_roles
from meddevice_suite.setup.sidebars import apply_sidebar_scope
from meddevice_suite.setup.stock import apply_stock_settings
from meddevice_suite.setup.workflows import apply_workflows


def apply_configuration() -> None:
	apply_desk_configuration()
	apply_sidebar_scope()
	apply_stock_settings()
	apply_store_roles()
	apply_lot_status()
	apply_receipt_fields()
	apply_supplier_approval_fields()
	apply_customer_licence_fields()
	apply_equipment_fields()
	apply_routing_fields()
	apply_warehouse_fields()
	apply_specification_fields()
	apply_master_versions()
	apply_item_master_fields()
	apply_roles()
	apply_workflows()
	apply_retention()
	apply_archive_indexes()
	apply_alerts()
	apply_lockout()
