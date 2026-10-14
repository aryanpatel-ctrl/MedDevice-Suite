// Audit anchors (URS-CMP-040): reviewers anchor the audit trail on request, before an
// inspection or an archive, and verify what the anchors show about the whole trail. The
// server checks the permission again (compliance/audit_anchor.py).
frappe.listview_settings["Audit Anchor"] = {
	onload(listview) {
		listview.page.add_inner_button(__("Anchor Now"), () =>
			frappe.xcall("meddevice_suite.compliance.audit_anchor.anchor_now").then((anchors) => {
				frappe.show_alert({
					message: anchors.length
						? anchors
								.map((anchor) =>
									__("{0}: anchor {1}, {2} entries", [
										__(anchor.ledger),
										anchor.anchor,
										anchor.entry_count,
									])
								)
								.join("<br>")
						: __("There is nothing to anchor yet."),
					indicator: "green",
				});
				listview.refresh();
			})
		);
		listview.page.add_inner_button(__("Verify Audit Trail"), () =>
			frappe
				.xcall("meddevice_suite.compliance.audit_anchor.verify_audit_trail")
				.then((problems) =>
					frappe.msgprint({
						title: problems.length
							? __("Integrity problem found")
							: __("Integrity verified"),
						indicator: problems.length ? "red" : "green",
						message: problems.length
							? problems
									.map((problem) => frappe.utils.escape_html(problem))
									.join("<br>")
							: __(
									"No anchored entry of the audit trail or the signatures is missing."
							  ),
					})
				)
		);
	},
};
