// Approved suppliers on the desk (module 4): the supplier's form shows QA's decision in force,
// and QA opens a new decision from it. Orders are checked on the server
// (master_data/supplier_approval.py).
frappe.ui.form.on("Supplier", {
	refresh(frm) {
		if (frm.is_new()) return;
		const approval = frm.doc.__onload?.supplier_approval;
		frm.dashboard.set_headline_alert(approval_text(approval), approval_colour(approval));
		if (frappe.user.has_role("Quality Manager")) {
			frm.add_custom_button(__("Decide Supplier Approval"), () =>
				frappe.new_doc("Supplier Approval", { supplier: frm.doc.name })
			);
		}
	},
});

function approval_text(approval) {
	if (!approval) return __("Not yet approved by QA: nothing can be ordered from this supplier.");
	const items = approval.item_codes.map((item) => frappe.utils.escape_html(item)).join(", ");
	if (approval.decision === "Not Approved") {
		return __("Not approved by QA ({0}): nothing can be ordered from this supplier.", [
			approval.name,
		]);
	}
	if (approval.decision === "Conditional") {
		const until = frappe.datetime.str_to_user(approval.valid_until);
		return approval.ended
			? __("Conditional approval {0} ended on {1}: QA decides the supplier again.", [
					approval.name,
					until,
			  ])
			: __("Approved conditionally until {0} ({1}) for: {2}", [until, approval.name, items]);
	}
	return __("Approved by QA ({0}) for: {1}", [approval.name, items]);
}

function approval_colour(approval) {
	if (!approval || approval.decision === "Not Approved" || approval.ended) return "red";
	return approval.decision === "Conditional" ? "orange" : "green";
}

// Version-controlled masters (URS-MST-001): a change to a master in use names an approved change
// request that lists it; the lookup offers only those (qms/change_control.py).
for (const doctype of [
	"Item",
	"BOM",
	"Routing",
	"Operation",
	"Workstation",
	"Supplier",
	"Customer",
	"Warehouse",
	"Asset",
	"Quality Inspection Template",
]) {
	frappe.ui.form.on(doctype, {
		setup(frm) {
			frm.set_query("mds_change_request", () => ({
				query: "meddevice_suite.qms.change_control.approved_changes_for",
				filters: { reference_doctype: frm.doctype, reference_name: frm.doc.name },
			}));
		},
	});
}
