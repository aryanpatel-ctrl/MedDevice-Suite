// Audit Trail button on regulated records, for users who may review the audit
// trail (URS-CMP-012). The server decides who sees it (compliance/audit_timeline.py).
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
});
