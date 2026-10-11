// Sampling plans (module 6) on the screen: QA chooses how a specification samples, and the form
// asks for the inspection level and the AQL only under ISO 2859-1
// (meddevice_suite/master_data/sampling.py).
const PASSWORD = "Sp3!aQl7#wRt5xVz"; // test users on the test site only
const QA = "cy.sampling.qa@job-profiles.example.com";
const FORM = ".page-container:visible";

function as_user(user) {
	// Leaves the previous person's page first (as in standard_flow.js).
	cy.window({ log: false }).then((win) => win.location.replace("about:blank"));
	cy.location("href", { log: false }).should("eq", "about:blank");
	if (user === "Administrator") cy.login();
	else cy.login(user, PASSWORD);
	cy.visit("/desk");
	cy.window().its("frappe.session.user").should("eq", user);
}

function field(name) {
	return cy.get(`${FORM} .frappe-control[data-fieldname="${name}"]`);
}

context("Sampling plans", () => {
	before(() => {
		cy.login();
		cy.visit("/desk");
		cy.insert_doc(
			"User",
			{
				email: QA,
				first_name: "QA Manager",
				send_welcome_email: 0,
				new_password: PASSWORD,
				role_profiles: [{ role_profile: "QA Manager" }],
			},
			true
		);
	});

	it("[URS-MST-009] the specification asks for a level and an AQL only under ISO 2859-1", () => {
		as_user(QA);
		cy.visit("/desk/quality-inspection-template/new");
		cy.window().should((win) =>
			expect(win.cur_frm?.doctype).to.equal("Quality Inspection Template")
		);
		cy.window().its("cur_frm.doc.mds_sampling").should("eq", "Fixed sample size");
		field("mds_sample_size").should("be.visible");
		field("mds_inspection_level").should("not.be.visible");
		field("mds_aql").should("not.be.visible");
		cy.window().then((win) => win.cur_frm.set_value("mds_sampling", "ISO 2859-1"));
		field("mds_inspection_level").should("be.visible");
		field("mds_aql").should("be.visible");
		field("mds_sample_size").should("not.be.visible");
		cy.window().then((win) => win.cur_frm.set_value("mds_sampling", "Every unit"));
		field("mds_inspection_level").should("not.be.visible");
		field("mds_sample_size").should("not.be.visible");
	});
});
