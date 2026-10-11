// Bill of materials (module 2) on the screen: Production drafts a bill, the form offers no plain
// Submit, and QA approves it with a signature (meddevice_suite/master_data/bom.py).
const PASSWORD = "Bm7!qRt2#vXw9pLz"; // test users on the test site only
const QA = "cy.bom.qa@job-profiles.example.com";
const PRODUCTION = "cy.bom.production@job-profiles.example.com";
const FORM = ".page-container:visible";

let bom; // a new draft bill each run

function as_user(user) {
	// Leaves the previous person's page first (as in standard_flow.js).
	cy.window({ log: false }).then((win) => win.location.replace("about:blank"));
	cy.location("href", { log: false }).should("eq", "about:blank");
	if (user === "Administrator") cy.login();
	else cy.login(user, PASSWORD);
	cy.visit("/desk");
	cy.window().its("frappe.session.user").should("eq", user);
}

function open_bom(name) {
	cy.visit(`/desk/bom/${encodeURIComponent(name)}`);
	cy.window().should((win) => {
		expect(win.cur_frm?.doctype).to.equal("BOM");
		expect(win.cur_frm.doc.name).to.equal(name);
		expect(win.cur_frm.doc.__onload?.bom_approval, "the approval shown").to.be.ok;
	});
}

function headline() {
	return cy.get(`${FORM} .form-message`);
}

// An approved item of the type, made and signed by QA through the API (module 1's own browser
// test covers doing that on the form).
function approved_item(code, type, extra = {}) {
	return cy
		.insert_doc("Item", {
			item_code: code,
			item_name: code,
			item_group: type === "Finished Good" ? "Products" : "Raw Material",
			mds_item_type: type,
			stock_uom: "Nos",
			gst_hsn_code: "39269099",
			is_stock_item: 1,
			mds_storage_condition: "Below 30 °C (browser test)",
			...extra,
		})
		.then(() =>
			cy.call("meddevice_suite.compliance.signing.sign_action", {
				doctype: "Item",
				name: code,
				action: "Approve",
				password: PASSWORD,
				reason: "Set up for the browser test",
			})
		);
}

function setup() {
	cy.login();
	cy.visit("/desk");
	for (const [email, profile] of [
		[QA, "QA Manager"],
		[PRODUCTION, "Production Supervisor"],
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
	const suffix = Date.now().toString(36).toUpperCase();
	as_user(QA);
	// Sub-assemblies need no specification of their own: the simplest approved product and part.
	approved_item(`MDS-UI-BOM-P-${suffix}`, "Sub-assembly (WIP)", {
		has_batch_no: 1,
		create_new_batch: 1,
		batch_number_series: `UIBP${suffix}-.####`,
		has_expiry_date: 1,
		shelf_life_in_days: 365,
	});
	approved_item(`MDS-UI-BOM-C-${suffix}`, "Consumable");
	as_user(PRODUCTION);
	cy.insert_doc("BOM", {
		item: `MDS-UI-BOM-P-${suffix}`,
		quantity: 1,
		is_active: 1,
		is_default: 1,
		rm_cost_as_per: "Valuation Rate",
		items: [{ item_code: `MDS-UI-BOM-C-${suffix}`, qty: 2, rate: 1 }],
	}).then((doc) => (bom = doc.name));
}

context("Bill of materials", () => {
	before(setup);

	it("[URS-MST-007] the draft bill says it awaits QA and offers no plain Submit", () => {
		as_user(PRODUCTION);
		open_bom(bom);
		headline().should("contain", "Ready for QA's approval");
		cy.get(`${FORM} .primary-action:visible`).should("not.exist");
		cy.get(`${FORM} button[data-label="${encodeURIComponent("Approve BOM")}"]`).should(
			"not.exist"
		);
	});

	it("[URS-MST-007] QA approves the bill on its form with a signature", () => {
		as_user(QA);
		open_bom(bom);
		cy.get(`${FORM} .primary-action:visible`).should("not.exist");
		cy.get(
			`${FORM} button[data-label="${encodeURIComponent("Approve BOM")}"]:visible`
		).click();
		cy.window().should((win) => {
			expect(win.cur_dialog?.display, "the signing dialog has opened").to.equal(true);
			expect(win.cur_dialog.title).to.equal("Sign: Approve");
		});
		cy.get('.modal.show textarea[data-fieldname="reason"]')
			.click()
			.type("Components and quantities checked (browser test)");
		cy.window()
			.its("cur_dialog")
			.should((dialog) =>
				expect(dialog.get_value("reason")).to.equal(
					"Components and quantities checked (browser test)"
				)
			);
		cy.get('.modal.show input[data-fieldname="password"]')
			.click()
			.type(PASSWORD, { log: false });
		cy.window()
			.its("cur_dialog")
			.should((dialog) => expect(dialog.get_value("password")).to.equal(PASSWORD));
		cy.get(".modal:visible .btn-primary").contains("Sign").click();
		cy.window().should((win) => expect(win.cur_frm.doc.docstatus, "approved").to.equal(1));
		headline().should("contain", "Approved by QA");
	});
});
