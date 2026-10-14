// Specifications (module 6) on the screen, as QA: the form says what a specification still needs
// before approval, QA names its test method, a controlled document in effect, and approves it on its
// form with a signature (plan D-33, D-34; meddevice_suite/master_data/specification.py).
const USERS = "meddevice_suite.qa.ui_users";
const JOURNEY = "meddevice_suite.qa.ui_journey";
const FORM = ".page-container:visible";

let password, users;
let test_method; // an SOP in effect, issued through document control
let specification; // a new draft specification each run: an approval is never undone

function as_user(user) {
	// Leaves the previous person's page first (as in standard_flow.js).
	cy.window({ log: false }).then((win) => win.location.replace("about:blank"));
	cy.location("href", { log: false }).should("eq", "about:blank");
	if (user === "Administrator") cy.login();
	else cy.login(user, password);
	cy.visit("/desk");
	cy.window().its("frappe.session.user").should("eq", user);
}

function open_specification() {
	cy.visit(`/desk/quality-inspection-template/${encodeURIComponent(specification)}`);
	cy.window().should((win) => {
		expect(win.cur_frm?.doctype).to.equal("Quality Inspection Template");
		expect(win.cur_frm.doc.name).to.equal(specification);
		expect(win.cur_frm.doc.__onload?.master_approval, "the approval shown").to.be.ok;
	});
}

function headline() {
	return cy.get(`${FORM} .form-message`); // Frappe's form message, where the banner is shown
}

function approve_button() {
	return cy.get(`${FORM} button[data-label="${encodeURIComponent("Approve")}"]`);
}

context("Specification approval", () => {
	before(() => {
		cy.login();
		cy.visit("/desk");
		cy.call(`${USERS}.prepare_job_users`).then(({ message }) => {
			password = message.password;
			users = message.users;
		});
		cy.then(() =>
			cy.call(`${JOURNEY}.prepare_effective_document`, { password, document_type: "SOP" })
		).then(({ message }) => (test_method = message));
		cy.insert_doc("Quality Inspection Parameter", { parameter: "Appearance" }, true);
		const suffix = Date.now().toString(36).toUpperCase();
		// A sub-assembly of its own, so that its final specification is new each run.
		cy.insert_doc("Item", {
			item_code: `MDS-UI-SPEC-${suffix}`,
			item_name: "Catheter tip, formed (browser test)",
			item_group: "Raw Material",
			mds_item_type: "Sub-assembly (WIP)",
			stock_uom: "Nos",
			gst_hsn_code: "39269099",
		}).then((item) => {
			as_user(users["QA Manager"]);
			cy.insert_doc("Quality Inspection Template", {
				quality_inspection_template_name: `UI specification ${suffix}`,
				mds_item: item.name,
				mds_stage: "Final",
				mds_sample_size: 5,
				item_quality_inspection_parameter: [
					{
						specification: "Appearance",
						mds_method: "Visual, under daylight (browser test)",
						numeric: 0,
						value: "Conforms",
					},
				],
			}).then((doc) => (specification = doc.name));
		});
	});

	it("[URS-MST-015] the form of a specification without its test method says so, and offers no approval", () => {
		as_user(users["QA Manager"]);
		open_specification();
		headline().should("contain", "Not yet approved by QA").and("contain", "its test method");
		approve_button().should("not.exist");
	});

	it("[URS-MST-014] QA names the test method and approves the specification on its form with a signature", () => {
		as_user(users["QA Manager"]);
		open_specification();
		cy.window().then((win) => win.cur_frm.set_value("mds_test_method", test_method));
		cy.window().its("cur_frm.doc.mds_test_method").should("eq", test_method);
		cy.window().then((win) => win.cur_frm.save());
		cy.window().should((win) => expect(win.cur_frm.is_dirty(), "saved").to.equal(false));
		open_specification(); // loaded again, with what its approval now needs
		headline().should("contain", "Ready for QA's approval");
		approve_button().filter(":visible").click();
		cy.window().should((win) => {
			expect(win.cur_dialog?.display, "the signing dialog has opened").to.equal(true);
			expect(win.cur_dialog.title).to.equal("Sign: Approve");
		});
		const reason = "Parameters, sampling and test method checked (browser test)";
		cy.get('.modal.show textarea[data-fieldname="reason"]').click().type(reason);
		cy.window()
			.its("cur_dialog")
			.should((dialog) => expect(dialog.get_value("reason")).to.equal(reason));
		cy.get('.modal.show input[data-fieldname="password"]')
			.click()
			.type(password, { log: false });
		cy.window()
			.its("cur_dialog")
			.should((dialog) => expect(dialog.get_value("password")).to.equal(password));
		cy.get(".modal:visible .btn-primary").contains("Sign").click();
		cy.window().should((win) => {
			expect(win.cur_frm.doc.mds_master_approval, "approved").to.equal("Approved");
			expect(win.cur_frm.doc.mds_master_approved_by).to.equal(users["QA Manager"]);
		});
		headline().should("contain", "Approved by QA");
		approve_button().should("not.exist");
	});
});
