"""Install and migrate entry point.

Everything this app configures on a site is applied from here, so a fresh
install and every ``bench migrate`` converge on the same declared state.
Each step must be idempotent.
"""

from meddevice_suite.compliance.alerts import apply_alerts
from meddevice_suite.compliance.archive import ensure_indexes as apply_archive_indexes
from meddevice_suite.compliance.retention import apply_retention
from meddevice_suite.setup.desk import apply_desk_configuration
from meddevice_suite.setup.roles import apply_roles
from meddevice_suite.setup.sidebars import apply_sidebar_scope
from meddevice_suite.setup.stock import apply_stock_settings
from meddevice_suite.setup.workflows import apply_workflows


def apply_configuration() -> None:
	apply_desk_configuration()
	apply_sidebar_scope()
	apply_stock_settings()
	apply_roles()
	apply_workflows()
	apply_retention()
	apply_archive_indexes()
	apply_alerts()
