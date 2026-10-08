// A change request's status in its title and in lists says where the change stands (module 36),
// instead of Frappe's generic "Draft" and "Submitted".
const CHANGE_COLOURS = {
	Draft: "red",
	"Under Assessment": "orange",
	"Awaiting Approval": "orange",
	"Awaiting Management Approval": "orange",
	Approved: "blue",
	Implemented: "blue",
	Closed: "green",
	Rejected: "gray",
	Withdrawn: "gray",
};

frappe.listview_settings["Change Request"] = {
	add_fields: ["status"],
	has_indicator_for_draft: true,
	get_indicator(doc) {
		return [__(doc.status), CHANGE_COLOURS[doc.status] || "gray", `status,=,${doc.status}`];
	},
};
