// Risk register (module 39): every hazard of the risk assessments in force, with its risks and controls.
frappe.query_reports["Risk Register"] = {
	filters: [
		{
			fieldname: "risk_management_file",
			label: __("Risk Management File"),
			fieldtype: "Link",
			options: "Risk Management File",
		},
		{
			fieldname: "residual_risk",
			label: __("Residual Risk"),
			fieldtype: "Select",
			options: "\nAcceptable\nReview (ALARP)\nUnacceptable",
		},
	],
};
