"""Move the audit trail's anchor file out of the private files folder Frappe serves (URS-CMP-040;
independent review of 2026-10-15).

Until this release the anchor file was kept in the site's `private/files`, where any desk user could
make a File record name it and then download it. It is now kept in `private/audit`, which Frappe
never serves. The anchors already in the file are moved there, followed by any the hourly job wrote
there between the release and this migrate; running the patch again moves nothing twice.
"""

from meddevice_suite.compliance.audit_anchor import move_former_anchor_file


def execute() -> None:
	move_former_anchor_file()
