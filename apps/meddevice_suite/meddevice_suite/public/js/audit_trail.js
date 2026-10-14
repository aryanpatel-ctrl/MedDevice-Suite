// Audit Trail and Verify Integrity buttons on regulated records, for users who may
// review the audit trail (URS-CMP-010, URS-CMP-012, URS-CMP-040). The server decides who sees
// them (compliance/audit_timeline.py) and checks again when verifying.
$(document).on("form-refresh", (event, frm) => {
	if (frm.is_new() || !frm.doc.__onload?.audit_trail) return;

	frm.add_custom_button(
		__("Audit Trail"),
		() =>
			frappe.set_route("List", "Audit Event", {
				reference_doctype: frm.doctype,
				reference_name: frm.doc.name,
			}),
		__("View")
	);

	frm.add_custom_button(
		__("Verify Integrity"),
		() =>
			frappe
				.xcall("meddevice_suite.compliance.audit_timeline.verify_record", {
					doctype: frm.doctype,
					name: frm.doc.name,
				})
				.then(({ audit_trail, signatures_intact, anchors }) => {
					const intact = !audit_trail.length && signatures_intact && !anchors.length;
					const findings = audit_trail.map((problem) =>
						frappe.utils.escape_html(problem)
					);
					if (!signatures_intact) {
						findings.push(__("The signatures on this record do not verify."));
					}
					// Entries removed anywhere in the trail, which no record's chain shows
					// (URS-CMP-040).
					if (anchors.length) {
						findings.push(__("Across the whole audit trail:"));
						findings.push(
							...anchors.map((problem) => frappe.utils.escape_html(problem))
						);
					}
					frappe.msgprint({
						title: intact ? __("Integrity verified") : __("Integrity problem found"),
						indicator: intact ? "green" : "red",
						message: intact
							? __("The audit trail and the signatures of {0} are intact.", [
									frm.doc.name,
							  ])
							: findings.join("<br>"),
					});
				}),
		__("View")
	);
});
