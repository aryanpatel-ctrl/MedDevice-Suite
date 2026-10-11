// Approved suppliers on the desk (module 4): the supplier's form shows QA's decision in force,
// and QA opens a new decision from it. Orders are checked on the server
// (master_data/supplier_approval.py).
frappe.ui.form.on("Supplier", {
	refresh(frm) {
		if (frm.is_new()) return;
		const approval = frm.doc.__onload?.supplier_approval;
		frm.dashboard.set_headline_alert(approval_text(approval), approval_colour(approval));
		show_rating(frm, frm.doc.__onload?.supplier_ratings || []);
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

// Supplier rating (URS-PUR-002, master_data/supplier_rating.py): the latest month's score, and
// any rating still waiting for QA's re-evaluation.
function show_rating(frm, ratings) {
	if (!ratings.length) return;
	const latest = ratings[0];
	const due = ratings.filter((rating) => rating.standing === "Re-evaluation Due");
	const text = due.length
		? __("Rated {0} for {1}, below {2}: due for QA's re-evaluation ({3}).", [
				latest.score,
				latest.period,
				latest.threshold,
				due.map((rating) => rating.name).join(", "),
		  ])
		: __("Rated {0} for {1} (re-evaluation below {2}).", [
				latest.score,
				latest.period,
				latest.threshold,
		  ]);
	frm.dashboard.add_comment(frappe.utils.escape_html(text), due.length ? "red" : "blue", true);
}

function approval_colour(approval) {
	if (!approval || approval.decision === "Not Approved" || approval.ended) return "red";
	return approval.decision === "Conditional" ? "orange" : "green";
}

// Item master (module 1): the item's form shows QA's approval, or what still stands in its way,
// and QA approves it with a signature. The server refuses a draft item on every record and
// checks the approval again (master_data/item_master.py).
const esign = frappe.provide("meddevice_suite.esign");

frappe.ui.form.on("Item", {
	refresh(frm) {
		if (frm.is_new()) return;
		const approval = frm.doc.__onload?.item_approval;
		if (!approval) return;
		frm.dashboard.set_headline_alert(
			item_approval_text(frm, approval),
			approval.status === "Approved" ? "green" : "orange"
		);
		if (
			approval.status !== "Approved" &&
			!frm.doc.has_variants &&
			!approval.missing.length &&
			!frm.is_dirty() &&
			frappe.user.has_role("Quality Manager")
		) {
			frm.add_custom_button(__("Approve Item"), () =>
				// Closing the dialog signs nothing; there is nothing more to do then.
				esign.sign(frm, "Approve", "Approved", { ask_reason: true }).catch(() => {})
			)
				.removeClass("btn-default")
				.addClass("btn-primary");
		}
	},
});

function item_approval_text(frm, approval) {
	if (approval.status === "Approved") {
		return __(
			"Approved by QA ({0}, {1}); its controlled fields change under change control.",
			[
				frappe.utils.escape_html(approval.approved_by_name || ""),
				frappe.datetime.str_to_user(approval.approved_at),
			]
		);
	}
	if (frm.doc.has_variants) {
		return __("A template: each of its variants is approved by QA on its own.");
	}
	if (approval.missing.length) {
		const missing = approval.missing.map((what) => frappe.utils.escape_html(what)).join("; ");
		return __("Not yet approved by QA, so used in no record. Still needed: {0}.", [missing]);
	}
	return __("Not yet approved by QA, so used in no record. Ready for QA's approval.");
}

// Bill of materials (module 2): the form says whether QA has approved it, or what stands in the
// way, and QA approves it with a signature, its only submit (master_data/bom.py).
frappe.ui.form.on("BOM", {
	refresh(frm) {
		if (frm.is_new()) return;
		const approval = frm.doc.__onload?.bom_approval;
		if (!approval) return;
		if (approval.approved) {
			frm.dashboard.set_headline_alert(
				approval.active
					? __("Approved by QA; which bill is in force changes under change control.")
					: __("Approved by QA but inactive: no work order is made from it."),
				approval.active ? "green" : "orange"
			);
			return;
		}
		if (frm.doc.docstatus !== 0) return;
		// The plain Submit is refused for everyone (master_data/bom.py): QA's signed approval is
		// offered instead, and no one is offered a button that only refuses.
		if (!frm.is_dirty()) frm.page.clear_primary_action();
		if (approval.missing.length) {
			const missing = approval.missing
				.map((what) => frappe.utils.escape_html(what))
				.join("; ");
			frm.dashboard.set_headline_alert(
				__("Awaiting QA's approval. Still needed: {0}.", [missing]),
				"orange"
			);
			return;
		}
		frm.dashboard.set_headline_alert(__("Ready for QA's approval."), "orange");
		if (!frm.is_dirty() && frappe.user.has_role("Quality Manager")) {
			frm.add_custom_button(__("Approve BOM"), () =>
				// Closing the dialog signs nothing; there is nothing more to do then.
				esign.sign(frm, "Approve", "Approved", { ask_reason: true }).catch(() => {})
			)
				.removeClass("btn-default")
				.addClass("btn-primary");
		}
	},
});

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

// Customer licences (module 5): the customer's form says whether it may be sold to today, and
// Sales records a licence from it. Orders and deliveries are checked on the server
// (master_data/customer_licence.py).
frappe.ui.form.on("Customer", {
	refresh(frm) {
		if (frm.is_new()) return;
		const licence = frm.doc.__onload?.customer_licence;
		if (!licence) return;
		if (licence.exempt) {
			frm.dashboard.set_headline_alert(
				__("Buys without a licence, as QA decided with a signature."),
				"blue"
			);
		} else if (licence.valid.length) {
			const held = licence.valid
				.map((row) =>
					__("{0} until {1}", [
						frappe.utils.escape_html(row.licence_number),
						frappe.datetime.str_to_user(row.valid_until),
					])
				)
				.join("; ");
			frm.dashboard.set_headline_alert(__("Licence verified by QA: {0}.", [held]), "green");
		} else {
			frm.dashboard.set_headline_alert(
				__(
					"No licence verified by QA is valid today: no sales order or delivery is made for this customer."
				),
				"red"
			);
		}
		if (frappe.model.can_create("Customer Licence")) {
			frm.add_custom_button(__("Record Licence"), () =>
				frappe.new_doc("Customer Licence", { customer: frm.doc.name })
			);
		}
		if (frappe.user.has_role("Quality Manager") && !frm.is_dirty()) {
			const act = licence.exempt ? "Require Licence" : "Exempt From Licence";
			frm.add_custom_button(__(act), () =>
				// Closing the dialog signs nothing; there is nothing more to do then.
				esign.sign(frm, act, "Approved", { ask_reason: true }).catch(() => {})
			);
		}
	},
});

// Equipment master (module 8): the asset's form says whether QA has approved the equipment, or
// what approval still needs, and QA approves it with a signature (master_data/equipment.py).
frappe.ui.form.on("Asset", {
	refresh(frm) {
		if (frm.is_new()) return;
		const approval = frm.doc.__onload?.equipment_approval;
		if (!approval) return;
		if (approval.approved) {
			frm.dashboard.set_headline_alert(
				__("Equipment approved by QA; what was approved changes under change control."),
				"green"
			);
			return;
		}
		if (approval.missing.length) {
			const missing = approval.missing
				.map((what) => frappe.utils.escape_html(what))
				.join("; ");
			frm.dashboard.set_headline_alert(
				__("Not yet approved by QA, so no inspection uses it. Still needed: {0}.", [
					missing,
				]),
				"orange"
			);
			return;
		}
		frm.dashboard.set_headline_alert(
			__("Not yet approved by QA, so no inspection uses it. Ready for QA's approval."),
			"orange"
		);
		if (approval.approvable && !frm.is_dirty() && frappe.user.has_role("Quality Manager")) {
			frm.add_custom_button(__("Approve Equipment"), () =>
				// Closing the dialog signs nothing; there is nothing more to do then.
				esign.sign(frm, "Approve", "Approved", { ask_reason: true }).catch(() => {})
			)
				.removeClass("btn-default")
				.addClass("btn-primary");
		}
	},
});

// Routings, operations and workstations (module 3): the form says whether QA has approved it, or
// what approval still needs, and QA approves it with a signature (master_data/routing.py).
for (const doctype of ["Operation", "Workstation", "Routing", "Warehouse"]) {
	frappe.ui.form.on(doctype, {
		refresh(frm) {
			if (frm.is_new()) return;
			const approval = frm.doc.__onload?.master_approval;
			if (!approval) return;
			if (approval.approved) {
				frm.dashboard.set_headline_alert(
					__("Approved by QA; what was approved changes under change control."),
					"green"
				);
				return;
			}
			if (approval.missing.length) {
				const missing = approval.missing
					.map((what) => frappe.utils.escape_html(what))
					.join("; ");
				frm.dashboard.set_headline_alert(
					__("Not yet approved by QA, so it is not used yet. Still needed: {0}.", [
						missing,
					]),
					"orange"
				);
				return;
			}
			frm.dashboard.set_headline_alert(
				__("Not yet approved by QA, so it is not used yet. Ready for QA's approval."),
				"orange"
			);
			if (!frm.is_dirty() && !frm.doc.disabled && frappe.user.has_role("Quality Manager")) {
				frm.add_custom_button(__("Approve"), () =>
					// Closing the dialog signs nothing; there is nothing more to do then.
					esign.sign(frm, "Approve", "Approved", { ask_reason: true }).catch(() => {})
				)
					.removeClass("btn-default")
					.addClass("btn-primary");
			}
		},
	});
}
