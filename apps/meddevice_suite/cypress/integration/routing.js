// Routings, operations and workstations (module 3) on the screen: Production records an operation
// with its work instruction, and QA approves it on its form with a signature
// (meddevice_suite/master_data/routing.py).
const USERS = "meddevice_suite.qa.ui_users";
const JOURNEY = "meddevice_suite.qa.ui_journey";
const FORM = ".page-container:visible";

let password, users, operation;

function as_user(user) {
	// Leaves the previous person's page first (as in standard_flow.js).
	cy.window({ log: false }).then((win) => win.location.replace("about:blank"));
	cy.location("href", { log: false }).should("eq", "about:blank");
	if (user === "Administrator") cy.login();
	else cy.login(user, password);
	cy.visit("/desk");
	cy.window().its("frappe.session.user").should("eq", user);
}

context("Routings, operations and workstations", () => {
	before(() => {
		cy.login();
		cy.visit("/desk");
		cy.call(`${USERS}.prepare_job_users`).then(({ message }) => {
			password = message.password;
			users = message.users;
		});
		// The journey's masters, among them a work instruction in effect.
		cy.then(() => cy.call(`${JOURNEY}.prepare_journey`, { password }));
		cy.then(() =>
			cy.call("frappe.client.get_value", {
				doctype: "Controlled Document",
				filters: { document_type: "Work Instruction", status: "Effective" },
				fieldname: "name",
			})
		).then(({ message }) => {
			as_user(users["Production Supervisor"]);
			cy.insert_doc("Operation", {
				name: `UI Trimming ${Date.now().toString(36).toUpperCase()}`,
				mds_work_instruction: message.name,
				mds_in_process_check: "Yes",
			}).then((doc) => (operation = doc.name));
		});
	});

	it("[URS-MST-012] the operation awaits QA, who approves it on its form with a signature", () => {
		as_user(users["QA Manager"]);
		cy.visit(`/desk/operation/${encodeURIComponent(operation)}`);
		cy.window().should((win) => {
			expect(win.cur_frm?.doctype).to.equal("Operation");
			expect(win.cur_frm.doc.__onload?.master_approval, "the approval shown").to.be.ok;
		});
		cy.get(`${FORM} .form-message`).should("contain", "Ready for QA's approval");
		cy.get(`${FORM} button[data-label="${encodeURIComponent("Approve")}"]:visible`).click();
		cy.window().should((win) => {
			expect(win.cur_dialog?.display, "the signing dialog has opened").to.equal(true);
			expect(win.cur_dialog.title).to.equal("Sign: Approve");
		});
		const reason = "Work instruction and in-process check in place (browser test)";
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
		cy.window().should((win) =>
			expect(win.cur_frm.doc.mds_master_approval, "approved").to.equal("Approved")
		);
		cy.get(`${FORM} .form-message`).should("contain", "Approved by QA");
	});
});
