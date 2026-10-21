// A decision's outcome in lists, instead of Frappe's generic "Submitted".
frappe.listview_settings["Lot Release"] = {
	add_fields: ["decision"],
	has_indicator_for_draft: true,
	get_indicator(doc) {
		const colours = {
			Release: "green",
			Reject: "red",
			Hold: "orange",
			"Return to Stock": "green",
			"Reject Return": "red",
			"Release by Concession": "green",
		};
		if (doc.docstatus === 0 && doc.decision === "Release by Concession") {
			// Plan D-35: QA has approved the concession; Management approves it next.
			return [
				__("Concession awaiting Management"),
				"orange",
				"docstatus,=,0|decision,=,Release by Concession",
			];
		}
		if (doc.docstatus === 0) return [__("Awaiting decision"), "gray", "docstatus,=,0"];
		return [__(doc.decision), colours[doc.decision] || "blue", `decision,=,${doc.decision}`];
	},
};
