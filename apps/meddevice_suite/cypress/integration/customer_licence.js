// Customer licences (module 5) on the screen: the customer's form says it may not be sold to,
// Sales records its licence, QA verifies it with a signature on the licence's form, and the
// customer's form then shows the licence (meddevice_suite/master_data/customer_licence.py).
const PASSWORD = "Cl4!vRt8#qWx2pLm"; // test users on the test site only
const QA = "cy.licence.qa@job-profiles.example.com";
const SALES = "cy.licence.sales@job-profiles.example.com";
const FORM = ".page-container:visible";
const TYPE = "Registration for the sale of medical devices (browser test)";

let customer, licence; // a new customer and its licence each run

function as_user(user) {
	// Leaves the previous person's page first (as in standard_flow.js).
	cy.window({ log: false }).then((win) => win.location.replace("about:blank"));
	cy.location("href", { log: false }).should("eq", "about:blank");
	if (user === "Administrator") cy.login();
	else cy.login(user, PASSWORD);
	cy.visit("/desk");
	cy.window().its("frappe.session.user").should("eq", user);
}

function open_form(doctype, name) {
	cy.visit(`/desk/${frappe_slug(doctype)}/${encodeURIComponent(name)}`);
	cy.window().should((win) => {
		expect(win.cur_frm?.doctype).to.equal(doctype);
		expect(win.cur_frm.doc.name).to.equal(name);
	});
}

function frappe_slug(doctype) {
	return doctype.toLowerCase().replace(/ /g, "-");
}

function headline() {
	return cy.get(`${FORM} .form-message`);
}

function setup() {
	cy.login();
	cy.visit("/desk");
	for (const [email, profile] of [
		[QA, "QA Manager"],
		[SALES, "Sales"],
	]) {
		cy.insert_doc(
			"User",
			{
				email,
				first_name: profile,
				send_welcome_email: 0,
				new_password: PASSWORD,
				role_profiles: [{ role_profile: profile }],
			},
			true
		);
	}
	cy.call("frappe.client.get_count", {
		doctype: "Customer Licence Type",
		filters: { name: TYPE },
	}).then((count) => {
		if (!count.message) cy.insert_doc("Customer Licence Type", { licence_type: TYPE });
	});
	const suffix = Date.now().toString(36).toUpperCase();
	as_user(SALES);
	cy.insert_doc("Customer", {
		customer_name: `UI Licence Distributor ${suffix}`,
		customer_group: "Commercial",
		territory: "India",
	}).then((doc) => (customer = doc.name));
}

context("Customer licences", () => {
	before(setup);

	it("[URS-MST-008] a customer without a verified licence is shown as not to be sold to", () => {
		as_user(SALES);
		open_form("Customer", customer);
		headline().should("contain", "No licence verified by QA is valid today");
		cy.get(`${FORM} button[data-label="${encodeURIComponent("Record Licence")}"]`).should(
			"be.visible"
		);
	});

	it("[URS-MST-008] Sales records the licence, which awaits QA and offers no Submit", () => {
		as_user(SALES);
		const today = new Date();
		const iso = (d) => d.toISOString().slice(0, 10);
		const from = new Date(today.getTime() - 30 * 864e5);
		const until = new Date(today.getTime() + 700 * 864e5);
		cy.insert_doc("Customer Licence", {
			customer,
			licence_type: TYPE,
			licence_number: `MD42-UI-${Date.now()}`,
			issuing_authority: "State Licensing Authority (browser test)",
			valid_from: iso(from),
			valid_until: iso(until),
			licence_copy: "/private/files/licence-browser-test.pdf",
		}).then((doc) => {
			licence = doc.name;
			open_form("Customer Licence", licence);
			headline().should("contain", "Awaiting QA's verification");
			// Sales may save the draft; no one is offered Frappe's Submit.
			cy.get(FORM).should(($form) =>
				expect($form.find(".primary-action:visible").text()).not.to.contain("Submit")
			);
			cy.get(`${FORM} button[data-label="${encodeURIComponent("Verify")}"]`).should(
				"not.exist"
			);
		});
	});

	it("[URS-MST-008] QA verifies the licence on its form with a signature", () => {
		as_user(QA);
		open_form("Customer Licence", licence);
		cy.get(`${FORM} .primary-action:visible`).should("not.exist");
		cy.get(`${FORM} button[data-label="${encodeURIComponent("Verify")}"]:visible`).click();
		cy.window().should((win) => {
			expect(win.cur_dialog?.display, "the signing dialog has opened").to.equal(true);
			expect(win.cur_dialog.title).to.equal("Sign: Verify");
		});
		const reason = "Checked against the authority's register (browser test)";
		cy.get('.modal.show textarea[data-fieldname="reason"]').click().type(reason);
		cy.window()
			.its("cur_dialog")
			.should((dialog) => expect(dialog.get_value("reason")).to.equal(reason));
		cy.get('.modal.show input[data-fieldname="password"]')
			.click()
			.type(PASSWORD, { log: false });
		cy.window()
			.its("cur_dialog")
			.should((dialog) => expect(dialog.get_value("password")).to.equal(PASSWORD));
		cy.get(".modal:visible .btn-primary").contains("Sign").click();
		cy.window().should((win) => {
			expect(win.cur_frm.doc.docstatus, "verified").to.equal(1);
			expect(win.cur_frm.doc.status).to.equal("Verified");
		});
		headline().should("contain", "Verified");
	});

	it("[URS-MST-008] the customer's form then shows the verified licence", () => {
		as_user(SALES);
		open_form("Customer", customer);
		headline().should("contain", "Licence verified by QA");
	});
});
