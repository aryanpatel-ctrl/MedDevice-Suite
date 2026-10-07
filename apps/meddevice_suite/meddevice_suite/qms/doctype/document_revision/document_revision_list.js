// A revision's status in its title and in lists says whether it may be used (module 30),
// instead of Frappe's generic "Submitted".
const REVISION_COLOURS = {
	Draft: "red",
	"In Review": "orange",
	"Awaiting Approval": "orange",
	Approved: "blue",
	Effective: "green",
	Superseded: "gray",
	Obsolete: "gray",
};

frappe.listview_settings["Document Revision"] = {
	add_fields: ["status"],
	has_indicator_for_draft: true,
	get_indicator(doc) {
		return [__(doc.status), REVISION_COLOURS[doc.status] || "gray", `status,=,${doc.status}`];
	},
};
