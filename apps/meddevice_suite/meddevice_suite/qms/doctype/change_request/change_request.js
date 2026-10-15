// A change request moves on only through its acts (module 36). The form offers each act to the
// person allowed to take it, lets each person edit only what is theirs at that stage, and says
// plainly where the change stands; the server checks every rule again (qms/change_control.py).
const esign = frappe.provide("meddevice_suite.esign");
const CHANGE_CONTROL = "meddevice_suite.qms.change_control";
const MANAGEMENT = ["Sales Manager", "Purchase Manager", "Stock Manager", "Manufacturing Manager"];
const REQUESTER_FIELDS = ["title", "description", "reason"];
const PLAN_FIELDS = ["change_type", "affected_records", "actions"];
const ASSESSMENT_FIELDS = [
	"impact_product_quality",
	"impact_risk",
	"impact_regulatory",
	"impact_validation",
	"impact_training",
	"impact_documents",
	"impact_stock",
	"impact_summary",
	"classification",
	"effectiveness_check_days",
];

frappe.ui.form.on("Change Request", {
	refresh(frm) {
		// Only the record types a change can affect, among those the person may look up.
		const types = selectable("Change Request Affected Record", "reference_doctype");
		const grid = (table) => frm.fields_dict[table].grid;
		grid("affected_records").update_docfield_property(
			"reference_doctype",
			"options",
			types.join("\n")
		);
		grid("actions").update_docfield_property(
			"target_doctype",
			"options",
			["", ...types].join("\n")
		);
		set_editable(frm);
		if (frm.is_new()) return;
		show_status(frm);
		if (frm.is_dirty()) return; // act on what is saved, never on unsaved edits
		offer_acts(frm);
	},
});

// A settings record (a single DocType) has no list to look it up in: it is named by its type.
function name_single(cdt, cdn, type_field, name_field) {
	const type = locals[cdt][cdn][type_field];
	if (!type) return;
	frappe.model.with_doctype(type, () => {
		if (frappe.get_meta(type).issingle) frappe.model.set_value(cdt, cdn, name_field, type);
	});
}

frappe.ui.form.on("Change Request Affected Record", {
	reference_doctype(frm, cdt, cdn) {
		name_single(cdt, cdn, "reference_doctype", "reference_name");
	},
});

frappe.ui.form.on("Change Request Action", {
	target_doctype(frm, cdt, cdn) {
		name_single(cdt, cdn, "target_doctype", "target_name");
	},
});

// The record types a field offers as declared (qms/change_control.py holds the same list),
// among those the person may look up. Reading a type lets one look it up, as on the server: the
// desk lists under "select" only the types granted it by name (found in the browser walk-through
// of 2026-10-15: QA was never offered a Document Revision).
function selectable(doctype, fieldname) {
	return frappe.meta
		.get_docfield(doctype, fieldname)
		.options.split("\n")
		.filter((type) => type && (frappe.model.can_select(type) || frappe.model.can_read(type)));
}

function roles() {
	const qa = frappe.user.has_role("Quality Manager");
	const management = MANAGEMENT.some((role) => frappe.user.has_role(role));
	return { qa, management };
}

function set_editable(frm) {
	const user = frappe.session.user;
	const { qa } = roles();
	const { status, requested_by } = frm.doc;
	const requester = frm.is_new() || (status === "Draft" && user === requested_by);
	const assessor = status === "Under Assessment" && qa;
	REQUESTER_FIELDS.forEach((field) => frm.set_df_property(field, "read_only", !requester));
	PLAN_FIELDS.forEach((field) =>
		frm.set_df_property(field, "read_only", !(requester || assessor))
	);
	ASSESSMENT_FIELDS.forEach((field) => frm.set_df_property(field, "read_only", !assessor));
}

function offer_acts(frm) {
	const user = frappe.session.user;
	const { qa, management } = roles();
	const { status, requested_by, qa_approver } = frm.doc;
	const requester = user === requested_by;
	const sign = (label, action, meaning, options = {}) => {
		const button = frm.add_custom_button(label, () =>
			esign.sign(frm, action, meaning, options).catch(() => {})
		);
		if (options.primary) button.removeClass("btn-default").addClass("btn-primary");
	};
	const decide = () => {
		sign(__("Return for Changes"), "Return", "Rejected", { ask_reason: true });
		sign(__("Reject"), "Reject", "Rejected", { ask_reason: true });
	};

	if (status === "Draft" && requester) {
		frm.add_custom_button(__("Send for Assessment"), () =>
			frappe
				.xcall(`${CHANGE_CONTROL}.submit_for_assessment`, { name: frm.doc.name })
				.then(() => frm.reload_doc())
		)
			.removeClass("btn-default")
			.addClass("btn-primary");
	}
	if (status === "Under Assessment" && qa) {
		sign(__("Assess"), "Assess", "Reviewed", { primary: true });
		decide();
	}
	if (status === "Awaiting Approval" && qa) {
		if (!requester) sign(__("Approve"), "Approve", "Approved", { primary: true });
		decide();
	}
	if (status === "Awaiting Management Approval" && (qa || management)) {
		if (management && !requester && user !== qa_approver) {
			sign(__("Approve"), "Approve", "Approved", { primary: true });
		}
		decide();
	}
	if (
		[
			"Draft",
			"Under Assessment",
			"Awaiting Approval",
			"Awaiting Management Approval",
		].includes(status) &&
		requester
	) {
		frm.add_custom_button(__("Withdraw"), () => withdraw(frm), __("Actions"));
	}
	if (status === "Approved") {
		const mine = (frm.doc.actions || []).filter(
			(row) => !row.done && (qa || row.action_owner === user)
		);
		if (mine.length) {
			frm.add_custom_button(__("Record Action Done"), () => record_done(frm, mine))
				.removeClass("btn-default")
				.addClass("btn-primary");
		}
	}
	const due = frm.doc.effectiveness_due_on;
	if (status === "Implemented" && qa && due && frappe.datetime.get_today() >= due) {
		sign(__("Verify: Effective"), "Verify Effective", "Verified", {
			ask_reason: true,
			primary: true,
		});
		sign(__("Verify: Not Effective"), "Verify Not Effective", "Verified", {
			ask_reason: true,
		});
	}
}

function withdraw(frm) {
	const dialog = new frappe.ui.Dialog({
		title: __("Withdraw {0}", [frm.doc.name]),
		fields: [{ fieldtype: "Small Text", fieldname: "reason", label: __("Reason"), reqd: 1 }],
		primary_action_label: __("Withdraw"),
		primary_action({ reason }) {
			frappe.xcall(`${CHANGE_CONTROL}.withdraw`, { name: frm.doc.name, reason }).then(() => {
				dialog.hide();
				frm.reload_doc();
			});
		},
	});
	dialog.show();
}

function record_done(frm, rows) {
	const date = (value) => frappe.datetime.str_to_user(value);
	const dialog = new frappe.ui.Dialog({
		title: __("Record an action as done"),
		fields: [
			{
				fieldtype: "Select",
				fieldname: "action",
				label: __("Action"),
				reqd: 1,
				options: rows.map((row) => ({
					label: `${row.idx}. ${row.description} (${__("due {0}", [
						date(row.due_date),
					])})`,
					value: row.name,
				})),
				default: rows[0].name,
			},
			{ fieldtype: "Small Text", fieldname: "note", label: __("What was done"), reqd: 1 },
			{
				fieldtype: "Select",
				fieldname: "result_doctype",
				label: __("Resulting record type"),
				options: ["", ...selectable("Change Request Action", "result_doctype")],
				description: __("The record the action produced, if any (a new revision, say)."),
			},
			{
				fieldtype: "Dynamic Link",
				fieldname: "result_name",
				label: __("Resulting record"),
				options: "result_doctype",
				depends_on: "result_doctype",
			},
		],
		primary_action_label: __("Record as Done"),
		primary_action(values) {
			frappe
				.xcall(`${CHANGE_CONTROL}.complete_action`, {
					name: frm.doc.name,
					action: values.action,
					note_text: values.note,
					result_doctype: values.result_doctype || null,
					result_name: values.result_name || null,
				})
				.then(() => {
					dialog.hide();
					frm.reload_doc();
				});
		},
	});
	dialog.show();
}

function show_status(frm) {
	const doc = frm.doc;
	const date = (value) => frappe.datetime.str_to_user(value);
	const reason = frappe.utils.escape_html(doc.decision_reason || "");
	const actions = doc.actions || [];
	const done = actions.filter((row) => row.done).length;
	const notices = {
		Draft: doc.decision_reason ? [__("Returned: {0}", [reason]), "orange"] : null,
		"Under Assessment": [__("Waiting for QA to assess its impact."), "blue"],
		"Awaiting Approval": [
			__("Assessed as a {0} change; waiting for QA approval.", [__(doc.classification)]),
			"orange",
		],
		"Awaiting Management Approval": [
			__("A Major change approved by QA; waiting for Management approval."),
			"orange",
		],
		Approved: [
			__("Approved on {0}. Carry out the actions: {1} of {2} done.", [
				date(doc.approved_on),
				done,
				actions.length,
			]),
			"blue",
		],
		Implemented: [
			__("Implemented on {0}; its effectiveness is checked on or after {1}.", [
				date(doc.implemented_on),
				date(doc.effectiveness_due_on),
			]),
			"blue",
		],
		Closed: [
			__("Closed on {0}: {1}.", [date(doc.closed_on), __(doc.effectiveness_outcome)]),
			doc.effectiveness_outcome === "Effective" ? "green" : "orange",
		],
		Rejected: [__("Rejected: {0}", [reason]), "red"],
		Withdrawn: [__("Withdrawn: {0}", [reason]), "gray"],
	};
	if (notices[doc.status]) frm.dashboard.set_headline_alert(...notices[doc.status]);
}
